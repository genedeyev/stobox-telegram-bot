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


def top_relevance(retrieved: list[RetrievedChunk], *, semantic_embeddings: bool = False) -> float:
    """Best ABSOLUTE relevance evidence for a retrieval set.

    The fused `score` is min-max normalized per query — the top hit is ~1.0 for
    ANY query, even a totally irrelevant corpus match, so thresholding on it
    made the IDK gate nearly inert. Preference order:
      1. LLM rerank score (absolute 0..1 judgment) when the reranker ran;
      2. raw cosine similarity, but only with a real semantic embedder
         (the offline hash embedder's cosines are noise);
      3. the legacy fused score, as the offline/test-mode fallback.
    """
    if not retrieved:
        return 0.0
    reranked = [rc.rerank_score for rc in retrieved if rc.rerank_score is not None]
    if reranked:
        return max(reranked)
    if semantic_embeddings:
        return max(rc.raw_score for rc in retrieved)
    return retrieved[0].score


class ConfidenceEngine:
    def __init__(
        self,
        threshold: float = 0.55,
        require_citations: bool = True,
        semantic_embeddings: bool = False,
    ) -> None:
        self.threshold = threshold
        self.require_citations = require_citations
        self.semantic_embeddings = semantic_embeddings

    def parse(self, answer: str) -> tuple[str, float | None, list[str] | None]:
        """Strip the CONFIDENCE/SOURCES trailer.

        Returns (clean_text, self_conf, sources) where sources is:
          * None      — the model emitted no SOURCES line at all;
          * []        — the model explicitly declared "SOURCES: none";
          * [labels…] — the sources the model claims grounded the answer.
        The None/[] distinction matters: an explicit "none" is the model
        admitting the answer is unsupported.
        """
        self_conf: float | None = None
        sources: list[str] | None = None
        m = _CONF_LINE.search(answer)
        if m:
            self_conf = float(m.group(1))
        s = _SRC_LINE.search(answer)
        if s:
            raw = s.group(1).strip()
            if raw.lower() in ("none", "-", "n/a"):
                sources = []
            else:
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
            top = top_relevance(retrieved, semantic_embeddings=self.semantic_embeddings)
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
