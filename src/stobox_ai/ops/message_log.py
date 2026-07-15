"""Internal message log — every message Stoby sees in a group, on the record.

Append-only, per-chat, capped, persisted to JSON so it survives restarts. This
is the audit/context layer: it lets admins see exactly who said what and when
(resolving "she was first" / "which message"), and gives Stoby a full transcript
to draw on beyond the short working-memory window.

Privacy note: this deliberately retains message text, so it's config-gated
(message_log.enabled) and capped per chat. It is separate from the per-user
profile retention controls.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class LoggedMessage:
    at: str
    chat_id: str
    chat_title: str
    user_id: str
    username: str | None
    display_name: str
    text: str
    message_id: str
    reply_to: str | None = None      # excerpt of the message this one replied to


class MessageLog:
    """Per-chat append-only log; chat_id -> list[LoggedMessage] (oldest first)."""

    def __init__(self, state_path: str | Path = "data/message_log.json",
                 cap_per_chat: int = 2000) -> None:
        self.path = Path(state_path)
        self.cap_per_chat = cap_per_chat
        self.chats = {}
        self._load()

    # -- write --------------------------------------------------------- #
    def append(self, *, chat_id: str, chat_title: str, user_id: str,
               username: str | None, display_name: str, text: str,
               message_id: str, reply_to: str | None = None) -> None:
        entry = LoggedMessage(
            at=_now_iso(), chat_id=str(chat_id), chat_title=chat_title or "",
            user_id=str(user_id), username=username, display_name=display_name or "",
            text=text[:2000], message_id=str(message_id),
            reply_to=(reply_to[:200] if reply_to else None),
        )
        bucket = self.chats.setdefault(str(chat_id), [])
        bucket.append(entry)
        if len(bucket) > self.cap_per_chat:
            del bucket[: len(bucket) - self.cap_per_chat]   # drop oldest
        self._save()

    # -- read ---------------------------------------------------------- #
    def recent(self, chat_id: str, n: int = 20) -> list[LoggedMessage]:
        return list(self.chats.get(str(chat_id), []))[-n:]

    def search(self, chat_id: str, term: str, n: int = 20) -> list[LoggedMessage]:
        term = term.lower().strip()
        if not term:
            return []
        hits = [m for m in self.chats.get(str(chat_id), [])
                if term in m.text.lower() or term in (m.display_name or "").lower()]
        return hits[-n:]

    def by_user(self, chat_id: str, who: str, n: int = 20) -> list[LoggedMessage]:
        """`who` matches a user_id, @username, or a display-name substring."""
        w = who.lower().lstrip("@").strip()
        out = [
            m for m in self.chats.get(str(chat_id), [])
            if w == m.user_id or (m.username and w == m.username.lower())
            or w in (m.display_name or "").lower()
        ]
        return out[-n:]

    def total(self, chat_id: str | None = None) -> int:
        if chat_id is not None:
            return len(self.chats.get(str(chat_id), []))
        return sum(len(v) for v in self.chats.values())

    # -- persistence --------------------------------------------------- #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            for cid, msgs in data.get("chats", {}).items():
                self.chats[str(cid)] = [LoggedMessage(**m) for m in msgs]
        except Exception as exc:  # noqa: BLE001
            log.error("msglog.load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"chats": {cid: [asdict(m) for m in msgs]
                                 for cid, msgs in self.chats.items()}}
            self.path.write_text(json.dumps(payload, indent=1))
        except Exception as exc:  # noqa: BLE001
            log.error("msglog.save_failed", error=str(exc))
