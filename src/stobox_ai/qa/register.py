"""Pending-question state: capture, dedupe, answer.

State persists to a JSON file (``data/qa_register.json`` — runtime state, not
committed) so pending questions survive restarts. Similar questions collapse
into one entry (token-Jaccard), collecting every asker so all of them get the
answer when it lands.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "are", "with", "that", "this", "you", "your",
         "what", "how", "why", "when", "does", "can", "stobox", "about"}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP}


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


@dataclass
class QAEntry:
    qid: int
    question: str
    status: str = "pending"                 # pending | answered
    answer: str = ""
    language: str = "en"
    created: str = ""
    answered_at: str = ""
    register_number: int | None = None      # section number in COMMUNITY-QA.md
    askers: list[dict] = field(default_factory=list)   # {channel, chat_id, message_id, user_key}
    ask_count: int = 1


class QARegister:
    def __init__(self, state_path: str | Path = "data/qa_register.json") -> None:
        self.path = Path(state_path)
        self.entries: dict[int, QAEntry] = {}
        self._load()

    # ------------------------------------------------------------------ #
    def capture(
        self, question: str, *, channel: str, chat_id: str, message_id: str,
        user_key: str, language: str = "en",
    ) -> tuple[QAEntry, bool]:
        """Record an unanswered question. Returns (entry, is_new). Similar
        pending questions collapse into one entry; the asker is appended so
        everyone gets the eventual answer."""
        question = question.strip()[:500]
        asker = {"channel": channel, "chat_id": chat_id,
                 "message_id": message_id, "user_key": user_key}
        for e in self.entries.values():
            if e.status == "pending" and _similar(e.question, question):
                if not any(a["user_key"] == user_key and a["chat_id"] == chat_id
                           for a in e.askers):
                    e.askers.append(asker)
                e.ask_count += 1
                self._save()
                return e, False
        qid = max(self.entries.keys(), default=0) + 1
        entry = QAEntry(
            qid=qid, question=question, language=language,
            created=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            askers=[asker],
        )
        self.entries[qid] = entry
        self._save()
        log.info("qa.captured", qid=qid, question=question[:80])
        return entry, True

    def answer(self, qid: int, text: str) -> QAEntry:
        entry = self.entries[qid]
        entry.answer = text.strip()
        entry.status = "answered"
        entry.answered_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        self._save()
        log.info("qa.answered", qid=qid)
        return entry

    def pending(self) -> list[QAEntry]:
        return sorted(
            (e for e in self.entries.values() if e.status == "pending"),
            key=lambda e: e.qid,
        )

    def get(self, qid: int) -> QAEntry | None:
        return self.entries.get(qid)

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.entries = {int(k): QAEntry(**v) for k, v in data.items()}
        except Exception as exc:  # noqa: BLE001 - corrupt state must not kill boot
            log.error("qa.state_load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({k: asdict(v) for k, v in self.entries.items()},
                           ensure_ascii=False, indent=1)
            )
        except Exception as exc:  # noqa: BLE001
            log.error("qa.state_save_failed", error=str(exc))
