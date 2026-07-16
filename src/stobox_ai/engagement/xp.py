"""XP / streaks / levels / leaderboard.

Rewards participation to drive retention: XP for helpful questions, correct quiz
answers, referrals, and daily activity (streaks). Persists to JSON (mount
/app/data in prod). All-time and weekly leaderboards; levels map XP to a title.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)

# (min_xp, title). Highest threshold ≤ xp wins.
LEVELS = [
    (0, "Newcomer"),
    (50, "Explorer"),
    (150, "Regular"),
    (350, "Tokenization Scholar"),
    (700, "RWA Expert"),
    (1500, "Community OG"),
]


def level_for(xp: int) -> tuple[int, str]:
    """Return (level_index, title) for an XP total."""
    idx, title = 0, LEVELS[0][1]
    for i, (threshold, name) in enumerate(LEVELS):
        if xp >= threshold:
            idx, title = i, name
    return idx, title


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _week() -> str:
    return datetime.now(UTC).strftime("%G-W%V")


@dataclass
class UserXP:
    user_key: str
    display_name: str = ""
    xp: int = 0
    xp_week: int = 0
    week_id: str = ""
    streak: int = 0
    best_streak: int = 0
    last_active: str = ""          # yyyy-mm-dd
    notified_level: int = 0        # highest level index already celebrated


class XPBook:
    def __init__(self, state_path: str | Path = "data/xp.json") -> None:
        self.path = Path(state_path)
        self.users: dict[str, UserXP] = {}
        self._load()

    def _rec(self, user_key: str, display_name: str = "") -> UserXP:
        rec = self.users.setdefault(user_key, UserXP(user_key=user_key))
        if display_name:
            rec.display_name = display_name
        if rec.week_id != _week():        # weekly reset
            rec.week_id, rec.xp_week = _week(), 0
        return rec

    def touch(self, user_key: str, display_name: str = "") -> tuple[int, bool]:
        """Register daily activity. Returns (streak, is_new_day). Awards a small
        streak bonus once per day."""
        rec = self._rec(user_key, display_name)
        today = _today()
        if rec.last_active == today:
            return rec.streak, False
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        rec.streak = rec.streak + 1 if rec.last_active == yesterday else 1
        rec.best_streak = max(rec.best_streak, rec.streak)
        rec.last_active = today
        bonus = min(2 + rec.streak, 10)   # grows with streak, capped
        rec.xp += bonus
        rec.xp_week += bonus
        self._save()
        return rec.streak, True

    def award(self, user_key: str, points: int, reason: str = "", display_name: str = "") -> int:
        rec = self._rec(user_key, display_name)
        rec.xp += points
        rec.xp_week += points
        self._save()
        log.info("xp.award", user=user_key, points=points, reason=reason, total=rec.xp)
        return rec.xp

    def check_levelup(self, user_key: str) -> str | None:
        """If the user has crossed into a new level since last celebrated, return
        the new title (and mark it), else None. Fire-and-forget for shout-outs."""
        rec = self.users.get(user_key)
        if not rec:
            return None
        idx, title = level_for(rec.xp)
        if idx > rec.notified_level:
            rec.notified_level = idx
            self._save()
            return title
        return None

    def get(self, user_key: str) -> UserXP | None:
        return self.users.get(user_key)

    def rank(self, user_key: str) -> int:
        ordered = sorted(self.users.values(), key=lambda u: u.xp, reverse=True)
        for i, u in enumerate(ordered, 1):
            if u.user_key == user_key:
                return i
        return 0

    def top(self, n: int = 10, weekly: bool = False) -> list[UserXP]:
        key = (lambda u: u.xp_week if u.week_id == _week() else 0) if weekly else (lambda u: u.xp)
        return sorted((u for u in self.users.values() if key(u) > 0), key=key, reverse=True)[:n]

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.users = {k: UserXP(**v) for k, v in data.items()}
        except Exception as exc:  # noqa: BLE001
            log.error("xp.load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(
                {k: asdict(v) for k, v in self.users.items()}, ensure_ascii=False, indent=1))
        except Exception as exc:  # noqa: BLE001
            log.error("xp.save_failed", error=str(exc))
