"""JSON log lines and the spend ledger.

Every answer writes one `answer` line with tokens, dollars, sources and verdict;
acceptance attempt A1-7 reads those lines. The daily spend is kept on the volume
and checked before every model call, so the cap stops the call, not the bill.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# USD per million tokens, from the Anthropic price table (checked 27.09.2026).
PRICES = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
CACHE_READ, CACHE_WRITE = 0.1, 1.25


def cost_usd(model: str, usage) -> float:
    pin, pout = PRICES.get(model, (5.00, 25.00))       # unknown model: price as Opus
    fresh = getattr(usage, "input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (fresh * pin + read * pin * CACHE_READ + write * pin * CACHE_WRITE + out * pout) / 1e6


class Log:
    """Minimal structured logger: one JSON object per line on stdout."""

    def __init__(self, stream=None) -> None:
        self._stream = stream            # None = sys.stdout, looked up at emit time

    def _emit(self, level: str, event: str, **kw) -> None:
        rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "level": level,
               "event": event, **kw}
        stream = self._stream or sys.stdout
        stream.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        stream.flush()

    def info(self, event: str, **kw) -> None:
        self._emit("info", event, **kw)

    def warning(self, event: str, **kw) -> None:
        self._emit("warning", event, **kw)

    def error(self, event: str, **kw) -> None:
        self._emit("error", event, **kw)


log = Log()


class SpendLedger:
    def __init__(self, state_dir: Path, daily_cap_usd: float) -> None:
        self.path = state_dir / "spend.json"
        self.cap = daily_cap_usd

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def today(self) -> float:
        d = self._read()
        return float(d.get(datetime.now(UTC).strftime("%Y-%m-%d"), 0.0))

    def allows(self) -> bool:
        return self.today() < self.cap

    def add(self, usd: float) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        d = {k: v for k, v in self._read().items() if k >= _days_ago(14)}
        d[day] = round(float(d.get(day, 0.0)) + usd, 6)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(self.path)
        except OSError as exc:
            log.error("ledger.write_failed", error=str(exc))


def _days_ago(n: int) -> str:
    return datetime.fromtimestamp(time.time() - n * 86400, UTC).strftime("%Y-%m-%d")
