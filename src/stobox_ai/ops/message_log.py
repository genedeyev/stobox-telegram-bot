"""Internal message log — every message Stoby sees in a group, on the record.

Append-only and built to hold ~months of history: writes are O(1) line-appends to
a JSONL file (no whole-file rewrite per message), the in-memory copy is pruned by
age (retention_days) and a hard per-chat ceiling, and the file is compacted on
load so it can't grow without bound across restarts.

Two jobs: (1) audit/context for admins (/log, /whosaid), and (2) recall — when
Stoby answers, `relevant()` surfaces older messages related to the question so he
can reference things said long before the short working-memory window.

Privacy note: this deliberately retains message text, so it's config-gated
(message_log.enabled) and separate from per-user profile retention.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "are", "with", "that", "this", "you", "your", "what",
         "how", "why", "when", "does", "can", "stobox", "about", "have", "was", "our"}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP}


def _now() -> datetime:
    return datetime.now(UTC)


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

    def __init__(self, state_path: str | Path = "data/message_log.jsonl",
                 cap_per_chat: int = 5000, retention_days: int = 90) -> None:
        self.path = Path(state_path)
        self.cap_per_chat = cap_per_chat
        self.retention = timedelta(days=retention_days)
        self.chats: dict[str, list[LoggedMessage]] = {}
        self._load()

    # -- write --------------------------------------------------------- #
    def append(self, *, chat_id: str, chat_title: str, user_id: str,
               username: str | None, display_name: str, text: str,
               message_id: str, reply_to: str | None = None) -> None:
        entry = LoggedMessage(
            at=_now().isoformat(), chat_id=str(chat_id), chat_title=chat_title or "",
            user_id=str(user_id), username=username, display_name=display_name or "",
            text=text[:2000], message_id=str(message_id),
            reply_to=(reply_to[:200] if reply_to else None),
        )
        bucket = self.chats.setdefault(str(chat_id), [])
        bucket.append(entry)
        self._prune(bucket)
        self._append_line(entry)

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

    def relevant(self, chat_id: str, query: str, *, n: int = 4,
                 exclude_recent: int = 12, min_overlap: int = 2) -> list[LoggedMessage]:
        """Older messages (beyond the working window) related to `query`, ranked
        by shared meaningful words. Returns [] when nothing clears the bar."""
        bucket = self.chats.get(str(chat_id), [])
        if len(bucket) <= exclude_recent:
            return []
        older = bucket[:-exclude_recent]
        q = _tokens(query)
        if not q:
            return []
        scored = []
        for m in older:
            overlap = len(q & _tokens(m.text))
            if overlap >= min_overlap:
                scored.append((overlap, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        # Return chronological order among the top matches (reads naturally).
        top = sorted((m for _, m in scored[:n]), key=lambda m: m.at)
        return top

    def total(self, chat_id: str | None = None) -> int:
        if chat_id is not None:
            return len(self.chats.get(str(chat_id), []))
        return sum(len(v) for v in self.chats.values())

    def purge_user(self, user_id: str) -> int:
        """GDPR erasure: drop every logged message from this user, all chats."""
        uid = str(user_id)
        removed = 0
        for bucket in self.chats.values():
            before = len(bucket)
            bucket[:] = [m for m in bucket if m.user_id != uid]
            removed += before - len(bucket)
        if removed:
            self._compact()
            log.info("msglog.user_purged", user=uid, removed=removed)
        return removed

    # -- pruning + persistence ----------------------------------------- #
    def _prune(self, bucket: list[LoggedMessage]) -> None:
        cutoff = (_now() - self.retention).isoformat()
        while bucket and bucket[0].at < cutoff:      # ISO strings sort chronologically
            bucket.pop(0)
        if len(bucket) > self.cap_per_chat:
            del bucket[: len(bucket) - self.cap_per_chat]

    def _append_line(self, entry: LoggedMessage) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry)) + "\n")
        except Exception as exc:  # noqa: BLE001
            log.error("msglog.append_failed", error=str(exc))

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.error("msglog.load_failed", error=str(exc))
            return
        from ..util import filter_dataclass_kwargs

        bad = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Per-line tolerance: one truncated append (unclean shutdown) must
            # not abandon the whole history AND the compaction that bounds it.
            try:
                m = LoggedMessage(**filter_dataclass_kwargs(LoggedMessage, json.loads(line)))
            except Exception:  # noqa: BLE001
                bad += 1
                continue
            self.chats.setdefault(m.chat_id, []).append(m)
        if bad:
            log.warning("msglog.skipped_bad_lines", count=bad)
        for bucket in self.chats.values():
            self._prune(bucket)
        self._compact()                          # trim the on-disk file to the pruned set

    def _compact(self) -> None:
        import os

        # Atomic: write the compacted log to a temp file, then swap it in — a
        # crash mid-compaction must never destroy the whole retained history.
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as f:
                for bucket in self.chats.values():
                    for m in bucket:
                        f.write(json.dumps(asdict(m)) + "\n")
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            log.error("msglog.compact_failed", error=str(exc))
