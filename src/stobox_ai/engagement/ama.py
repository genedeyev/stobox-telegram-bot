"""AMA collector — crowd-sourced, community-ranked AMA prep.

Members submit questions with /ama during an open collection window; similar
questions merge (each merge is an implicit upvote); everyone upvotes with a tap.
Admins get a vote-ranked list — zero manual triage. Persisted to JSON.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "are", "with", "that", "this", "you", "your", "what",
         "how", "why", "when", "does", "can", "will", "stobox", "about", "any"}


def _tokens(t: str) -> set[str]:
    return {w for w in _TOKEN.findall(t.lower()) if w not in _STOP}


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    # Overlap coefficient (min-based) — more forgiving than Jaccard for short,
    # differently-phrased questions ("when's the burn deadline?" ≈ "burn
    # deadline time?"). Requires ≥2 shared content words to avoid over-merging.
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return False
    shared = len(ta & tb)
    return shared >= 2 and shared / min(len(ta), len(tb)) >= threshold


@dataclass
class AMAQuestion:
    qid: int
    text: str
    submitter_name: str = ""
    submitter_key: str = ""
    voters: list[str] = field(default_factory=list)   # user_keys who upvoted
    created: str = ""

    @property
    def votes(self) -> int:
        return len(set(self.voters))


class AMABook:
    def __init__(self, state_path: str | Path = "data/ama.json") -> None:
        self.path = Path(state_path)
        self.open: bool = False
        self.topic: str = ""
        self.questions: dict[int, AMAQuestion] = {}
        self._load()

    def open_session(self, topic: str = "") -> None:
        self.open = True
        self.topic = topic
        self._save()

    def close_session(self) -> None:
        self.open = False
        self._save()

    def clear(self) -> None:
        self.questions.clear()
        self.open = False
        self.topic = ""
        self._save()

    def submit(self, text: str, user_key: str, name: str = "") -> tuple[AMAQuestion, bool]:
        """Add a question (or merge into a similar one + upvote). Returns
        (entry, is_new)."""
        text = text.strip()[:400]
        for q in self.questions.values():
            if _similar(q.text, text):
                if user_key not in q.voters:
                    q.voters.append(user_key)
                self._save()
                return q, False
        qid = max(self.questions, default=0) + 1
        q = AMAQuestion(qid=qid, text=text, submitter_name=name, submitter_key=user_key,
                        voters=[user_key], created=datetime.now(UTC).strftime("%Y-%m-%d %H:%M"))
        self.questions[qid] = q
        self._save()
        return q, True

    def upvote(self, qid: int, user_key: str) -> int:
        q = self.questions.get(qid)
        if not q:
            return -1
        if user_key in q.voters:
            q.voters.remove(user_key)          # toggle off
        else:
            q.voters.append(user_key)
        self._save()
        return q.votes

    def get(self, qid: int) -> AMAQuestion | None:
        return self.questions.get(qid)

    def ranked(self, n: int | None = None) -> list[AMAQuestion]:
        items = sorted(self.questions.values(), key=lambda q: (q.votes, q.qid), reverse=True)
        return items[:n] if n else items

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        from ..ops.statefile import load_json_guarded

        data = load_json_guarded(self.path, label="ama")
        if data is None:
            return
        from ..util import filter_dataclass_kwargs

        try:
            self.open = data.get("open", False)
            self.topic = data.get("topic", "")
            self.questions = {
                int(k): AMAQuestion(**filter_dataclass_kwargs(AMAQuestion, v))
                for k, v in data.get("questions", {}).items()
            }
        except Exception as exc:  # noqa: BLE001
            log.error("ama.load_failed", error=str(exc))

    def _save(self) -> None:
        from ..ops.statefile import save_json_atomic

        try:
            save_json_atomic(self.path, {
                "open": self.open, "topic": self.topic,
                "questions": {k: asdict(v) for k, v in self.questions.items()},
            })
        except Exception as exc:  # noqa: BLE001
            log.error("ama.save_failed", error=str(exc))
