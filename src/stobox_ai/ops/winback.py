"""Win-back nudges for quiet, opted-in members (strictly opt-in, DM only).

A member who subscribed to a topic (/subscribe) but has gone quiet for a while
gets ONE gentle, value-first check-in DM — never a guilt trip, never a sales
push, always a one-tap way out. We only ever message people who explicitly
opted into topic DMs, and a per-user cooldown means we never nag: at most one
nudge, then silence for `cooldown_days`.

This book only tracks *when we last nudged each chat* so restarts and repeated
job ticks never double-send. The consent + inactivity checks live in the job.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class WinBackBook:
    def __init__(self, state_path: str | Path = "data/winback.json") -> None:
        self.path = Path(state_path)
        self.last_nudged: dict[str, str] = {}   # chat_id -> ISO timestamp
        self._load()

    def can_nudge(self, chat_id: str, cooldown_days: int) -> bool:
        """True if this chat was never nudged, or the cooldown has elapsed."""
        stamp = self.last_nudged.get(chat_id)
        if not stamp:
            return True
        try:
            last = datetime.fromisoformat(stamp)
        except ValueError:
            return True
        return (_now() - last).days >= cooldown_days

    def mark_nudged(self, chat_id: str) -> None:
        self.last_nudged[chat_id] = _now().isoformat()
        self._save()

    # -- persistence --------------------------------------------------- #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.last_nudged = {str(k): str(v) for k, v in data.get("last_nudged", {}).items()}
        except Exception as exc:  # noqa: BLE001
            log.error("winback.load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"last_nudged": self.last_nudged}, indent=1))
        except Exception as exc:  # noqa: BLE001
            log.error("winback.save_failed", error=str(exc))
