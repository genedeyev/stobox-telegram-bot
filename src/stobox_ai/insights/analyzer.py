"""Deterministic analysis over decision-log records.

No LLM here — pure aggregation so it's fast, cheap, and unit-testable. The FAQ
generator (faq.py) layers the reasoner on top of ``cluster_questions``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..analytics.logger import Decision

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {
    "the", "and", "for", "are", "with", "that", "this", "you", "your", "from",
    "have", "has", "can", "will", "not", "does", "did", "how", "what", "why",
    "when", "which", "who", "about", "into", "stobox", "please", "tell",
}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(slots=True)
class QuestionCluster:
    representative: str                       # the exemplar question
    count: int
    members: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    avg_confidence: float = 0.0
    unresolved: int = 0                       # members answered with low confidence

    @property
    def is_gap(self) -> bool:
        """A recurring question the bot struggled to answer → doc gap."""
        return self.count >= 2 and (self.unresolved / self.count) >= 0.5


def _questions(decisions: list[Decision]) -> list[Decision]:
    # Only real questions that needed docs (skip small talk, moderation-only).
    return [d for d in decisions if d.question and d.mode != "moderator"]


def cluster_questions(
    decisions: list[Decision], *, threshold: float = 0.5, min_len: int = 6
) -> list[QuestionCluster]:
    """Greedy single-pass clustering of similar questions by token Jaccard.

    Deterministic: input order in → same clusters out. Representative is the
    longest (most specific) member.
    """
    clusters: list[dict] = []
    for d in _questions(decisions):
        q = d.question.strip()
        if len(q) < min_len:
            continue
        toks = _tokens(q)
        low_conf = d.confidence == "low"
        best = None
        best_sim = threshold
        for c in clusters:
            sim = _jaccard(toks, c["tokens"])
            if sim >= best_sim:
                best, best_sim = c, sim
        if best is None:
            clusters.append({
                "tokens": set(toks),
                "members": [q],
                "topics": Counter(d.meta.get("topics", []) if d.meta else []),
                "conf": [d.confidence_score],
                "unresolved": int(low_conf),
            })
        else:
            best["tokens"] |= toks
            best["members"].append(q)
            best["topics"].update(d.meta.get("topics", []) if d.meta else [])
            best["conf"].append(d.confidence_score)
            best["unresolved"] += int(low_conf)

    out = [
        QuestionCluster(
            representative=max(c["members"], key=len),
            count=len(c["members"]),
            members=c["members"],
            topics=[t for t, _ in c["topics"].most_common(5)],
            avg_confidence=round(sum(c["conf"]) / len(c["conf"]), 3),
            unresolved=c["unresolved"],
        )
        for c in clusters
    ]
    out.sort(key=lambda c: c.count, reverse=True)
    return out


def documentation_gaps(decisions: list[Decision]) -> list[QuestionCluster]:
    """Recurring questions the bot could not confidently answer (missing docs)."""
    return [c for c in cluster_questions(decisions) if c.is_gap]


def potential_leads(decisions: list[Decision]) -> list[dict]:
    """Users who showed buying signals (sales mode or captured lead)."""
    by_user: dict[str, dict] = {}
    for d in decisions:
        signal = d.mode == "sales_assistant" or d.lead_captured
        if not signal:
            continue
        u = by_user.setdefault(
            d.user_key, {"user_key": d.user_key, "touches": 0, "captured": False, "last_q": ""}
        )
        u["touches"] += 1
        u["captured"] = u["captured"] or d.lead_captured
        u["last_q"] = d.question[:140] or u["last_q"]
    return sorted(by_user.values(), key=lambda u: (u["captured"], u["touches"]), reverse=True)


def sentiment_proxy(decisions: list[Decision]) -> dict:
    """Heuristic community-health proxy (NOT true sentiment analysis).

    Derived from answerability + moderation load, so it's honest about being a
    proxy. A real sentiment model can replace this behind the same shape.
    """
    n = len(decisions) or 1
    low = sum(1 for d in decisions if d.confidence == "low")
    mod = sum(1 for d in decisions if d.moderation != "none")
    escal = sum(1 for d in decisions if d.escalated)
    health = max(0.0, 1.0 - (low / n) * 0.5 - (mod / n) * 0.8 - (escal / n) * 0.5)
    label = "healthy" if health > 0.7 else "watch" if health > 0.4 else "at-risk"
    return {
        "health_score": round(health, 3),
        "label": label,
        "unanswered_rate": round(low / n, 3),
        "moderation_rate": round(mod / n, 3),
        "note": "heuristic proxy from answerability + moderation load, not NLP sentiment",
    }
