"""Confidence engine — the anti-hallucination gate.

Combines two signals into a final confidence score:
  * retrieval strength (top fused score + coverage), and
  * the model's self-reported CONFIDENCE line.

If the final score is below the configured threshold, the answer is replaced
with an honest "I don't know based on the current documentation" and flagged for
escalation. Also parses the SOURCES line the synthesis prompt emits.
"""

from __future__ import annotations

import re

from ..core.types import Confidence
from ..knowledge.models import RetrievedChunk

_CONF_LINE = re.compile(r"CONFIDENCE:\s*([01](?:\.\d+)?)", re.I)
_SRC_LINE = re.compile(r"SOURCES:\s*(.+)", re.I)


class ConfidenceEngine:
    def __init__(self, threshold: float = 0.55, require_citations: bool = True) -> None:
        self.threshold = threshold
        self.require_citations = require_citations

    def parse(self, answer: str) -> tuple[str, float | None, list[str]]:
        """Strip the CONFIDENCE/SOURCES trailer; return (clean_text, self_conf, sources)."""
        self_conf: float | None = None
        sources: list[str] = []
        m = _CONF_LINE.search(answer)
        if m:
            self_conf = float(m.group(1))
        s = _SRC_LINE.search(answer)
        if s:
            raw = s.group(1).strip()
            if raw.lower() not in ("none", "-", "n/a"):
                sources = [x.strip() for x in raw.split(",") if x.strip()]
        clean = _CONF_LINE.sub("", answer)
        clean = _SRC_LINE.sub("", clean).strip()
        return clean, self_conf, sources

    def score(
        self, retrieved: list[RetrievedChunk], self_conf: float | None, cited: bool
    ) -> float:
        if not retrieved:
            retrieval_signal = 0.0
        else:
            top = retrieved[0].score
            coverage = min(1.0, len(retrieved) / 3)
            retrieval_signal = 0.7 * top + 0.3 * coverage
        model_signal = self_conf if self_conf is not None else retrieval_signal
        final = 0.55 * retrieval_signal + 0.45 * model_signal
        if self.require_citations and not cited:
            final *= 0.5  # unsupported claims are heavily discounted
        return round(min(1.0, final), 3)

    def label(self, score: float) -> Confidence:
        return Confidence.from_score(score, self.threshold)

    def below_threshold(self, score: float) -> bool:
        return score < self.threshold
