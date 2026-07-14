"""Rate limiting + global spend cap (ARCHITECTURE.md §7).

Two guards, both in-memory (fine for a single worker; swap for Redis if you
scale horizontally):

  * per-user token bucket — N messages/minute and M messages/day. Over-limit
    users get a cheap static reply, never an LLM call.
  * global daily output-token cap — once the day's Anthropic output budget is
    spent, the bot degrades to static answers and alerts admins, so a runaway
    can't drain the key.

All time reads use wall clock at call time (safe in the app runtime).
"""

from __future__ import annotations

import enum
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime


class RateStatus(str, enum.Enum):
    OK = "ok"
    PER_MINUTE = "per_minute"
    PER_DAY = "per_day"
    GLOBAL_CAP = "global_cap"


@dataclass(slots=True)
class RateDecision:
    status: RateStatus
    retry_hint: str = ""

    @property
    def allowed(self) -> bool:
        return self.status == RateStatus.OK


class RateLimiter:
    def __init__(
        self,
        per_minute: int = 20,
        per_day: int = 100,
        global_daily_output_tokens: int | None = 2_000_000,
    ) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self.global_cap = global_daily_output_tokens
        self._minute: dict[str, deque[float]] = defaultdict(deque)
        self._day: dict[str, tuple[str, int]] = {}   # user -> (yyyy-mm-dd, count)
        self._spent_day: str = ""
        self._spent_tokens: int = 0

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def check(self, user_key: str) -> RateDecision:
        # Global spend cap first — protects the whole key.
        if self.global_cap is not None and self._spent_today() >= self.global_cap:
            return RateDecision(RateStatus.GLOBAL_CAP,
                                "The bot is at today's usage limit. Please try again later.")

        now = time.monotonic()
        # Per-minute sliding window.
        dq = self._minute[user_key]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.per_minute:
            return RateDecision(RateStatus.PER_MINUTE, "You're sending messages too quickly — please slow down.")

        # Per-day counter.
        today = self._today()
        day, count = self._day.get(user_key, (today, 0))
        if day != today:
            day, count = today, 0
        if count >= self.per_day:
            return RateDecision(RateStatus.PER_DAY, "You've reached today's message limit. Please continue tomorrow.")

        # Accept: record usage.
        dq.append(now)
        self._day[user_key] = (day, count + 1)
        return RateDecision(RateStatus.OK)

    def record_spend(self, output_tokens: int) -> None:
        if self._spent_day != self._today():
            self._spent_day, self._spent_tokens = self._today(), 0
        self._spent_tokens += max(0, output_tokens)

    def _spent_today(self) -> int:
        if self._spent_day != self._today():
            self._spent_day, self._spent_tokens = self._today(), 0
        return self._spent_tokens

    @property
    def over_global_cap(self) -> bool:
        return self.global_cap is not None and self._spent_today() >= self.global_cap
