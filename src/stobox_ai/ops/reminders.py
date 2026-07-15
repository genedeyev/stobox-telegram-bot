"""Migration-reminder subscriptions (strictly opt-in).

Users subscribe with /remindme (DM only) and get deadline reminders at fixed
thresholds before the STBU burn deadline, plus one claims-open notice. State
persists to JSON: subscribers + which threshold blasts were already sent, so
restarts never double-send. Unsubscribe any time with /stopreminders.

This respects the never-initiate rule: only users who explicitly opted in are
messaged, and every reminder carries the one-tap way out.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)

# Days-before-deadline at which a reminder fires.
THRESHOLDS = [30, 14, 7, 3, 1, 0]


class ReminderBook:
    def __init__(self, state_path: str | Path = "data/reminders.json") -> None:
        self.path = Path(state_path)
        self.subscribers: dict[str, str] = {}   # chat_id -> language
        self.sent: list[str] = []               # threshold tags already blasted
        self._load()

    def subscribe(self, chat_id: str, language: str = "en") -> bool:
        """Returns True if newly subscribed."""
        new = chat_id not in self.subscribers
        self.subscribers[chat_id] = language
        self._save()
        if new:
            log.info("reminders.subscribed", chat=chat_id)
        return new

    def unsubscribe(self, chat_id: str) -> bool:
        existed = self.subscribers.pop(chat_id, None) is not None
        self._save()
        return existed

    def is_subscribed(self, chat_id: str) -> bool:
        return chat_id in self.subscribers

    def was_sent(self, tag: str) -> bool:
        return tag in self.sent

    def mark_sent(self, tag: str) -> None:
        if tag not in self.sent:
            self.sent.append(tag)
            self._save()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.subscribers = dict(data.get("subscribers", {}))
            self.sent = list(data.get("sent", []))
        except Exception as exc:  # noqa: BLE001
            log.error("reminders.load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(
                {"subscribers": self.subscribers, "sent": self.sent}, indent=1
            ))
        except Exception as exc:  # noqa: BLE001
            log.error("reminders.save_failed", error=str(exc))
