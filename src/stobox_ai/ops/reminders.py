"""Migration-reminder subscriptions (strictly opt-in).

Users subscribe with /remindme (DM only) and get deadline reminders at fixed
thresholds before the STBU burn deadline, plus one claims-open notice. State
persists to JSON: subscribers + which threshold blasts were already sent, so
restarts never double-send. Unsubscribe any time with /stopreminders.

This respects the never-initiate rule: only users who explicitly opted in are
messaged, and every reminder carries the one-tap way out.
"""

from __future__ import annotations

from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)

# Days-before-deadline at which a reminder fires.
THRESHOLDS = [30, 14, 7, 3, 1, 0]


class ReminderBook:
    def __init__(self, state_path: str | Path = "data/reminders.json") -> None:
        self.path = Path(state_path)
        self.subscribers: dict[str, str] = {}   # chat_id -> language
        self.sent: list[str] = []               # threshold tags fully blasted
        # Partial-blast progress: tag -> chat_ids already delivered. Persisted
        # per recipient so a crash/flood-wait mid-blast neither double-sends
        # (delivered users are skipped on retry) nor loses the rest (the tag
        # is only marked fully sent after a clean pass).
        self.delivered: dict[str, list[str]] = {}
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
        """The blast for ``tag`` completed a clean pass over all subscribers."""
        if tag not in self.sent:
            self.sent.append(tag)
        self.delivered.pop(tag, None)   # per-user progress no longer needed
        self._save()

    def was_delivered(self, tag: str, chat_id: str) -> bool:
        return chat_id in self.delivered.get(tag, ())

    def mark_delivered(self, tag: str, chat_id: str) -> None:
        self.delivered.setdefault(tag, [])
        if chat_id not in self.delivered[tag]:
            self.delivered[tag].append(chat_id)
            self._save()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        from .statefile import load_json_guarded

        data = load_json_guarded(self.path, label="reminders")
        if data is None:
            return
        try:
            self.subscribers = dict(data.get("subscribers", {}))
            self.sent = list(data.get("sent", []))
            self.delivered = {str(k): list(v) for k, v in data.get("delivered", {}).items()}
        except Exception as exc:  # noqa: BLE001
            log.error("reminders.load_failed", error=str(exc))

    def _save(self) -> None:
        from .statefile import save_json_atomic

        try:
            save_json_atomic(self.path, {
                "subscribers": self.subscribers, "sent": self.sent,
                "delivered": self.delivered,
            })
        except Exception as exc:  # noqa: BLE001
            log.error("reminders.save_failed", error=str(exc))
