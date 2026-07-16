"""Hybrid retrieval: BM25 (lexical) + vector (semantic) fused, optional LLM
rerank, optional multi-hop.

Fusion uses weighted min-max normalized scores from both retrievers. The BM25
index is cached and rebuilt only when the store's ``version`` changes.
"""

from __future__ import annotations

import re

from ..config import Config
from ..llm.base import ChatMessage, EmbeddingProvider, LLMProvider
from ..logging import get_logger
from ..util import extract_json
from .models import Chunk, RetrievedChunk
from .store import VectorStore

log = get_logger(__name__)
_TOKEN = re.compile(r"[a-zA-Z0-9]{2,}")


def _tok(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def _minmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class HybridRetriever:
    def __init__(
        self,
        store: VectorStore,
        embedder: EmbeddingProvider,
        config: Config,
        reasoner: LLMProvider | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.reasoner = reasoner
        r = config.section("knowledge.retrieval")
        self.top_k = int(r.get("top_k", 8))
        self.final_k = int(r.get("final_k", 5))
        hybrid = r.get("hybrid") or {}
        self.vec_w = float(hybrid.get("vector_weight", 0.6))
        self.bm25_w = float(hybrid.get("bm25_weight", 0.4))
        self.do_rerank = bool(r.get("rerank", True))
        self.multi_hop = bool(r.get("multi_hop", True))
        self.max_hops = int(r.get("max_hops", 2))

        self._bm25 = None
        self._bm25_chunks: list[Chunk] = []
        self._bm25_version = -1

    async def _ensure_bm25(self) -> None:
        if self._bm25_version == self.store.version and self._bm25 is not None:
            return
        from rank_bm25 import BM25Okapi

        chunks = await self.store.all_chunks()
        self._bm25_chunks = chunks
        corpus = [_tok(c.text + " " + " ".join(c.keywords)) for c in chunks]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._bm25_version = self.store.version

    async def _bm25_search(self, query: str) -> list[tuple[Chunk, float]]:
        await self._ensure_bm25()
        if not self._bm25:
            return []
        # get_scores returns a numpy array; cast to native float so scores stay
        # JSON-serializable all the way through to the decision log / Postgres.
        scores = self._bm25.get_scores(_tok(query))
        ranked = sorted(
            ((c, float(s)) for c, s in zip(self._bm25_chunks, scores, strict=False)),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[: self.top_k]

    async def _fuse(self, query: str) -> dict[str, RetrievedChunk]:
        q_emb = await self.embedder.embed_one(query)
        vec = await self.store.search(q_emb, self.top_k)
        bm = await self._bm25_search(query)

        raw_scores = {c.chunk_id: float(s) for c, s in vec}   # absolute cosine sim
        vec_scores = _minmax({c.chunk_id: s for c, s in vec})
        bm_scores = _minmax({c.chunk_id: s for c, s in bm})
        by_id: dict[str, Chunk] = {c.chunk_id: c for c, _ in vec}
        by_id.update({c.chunk_id: c for c, _ in bm})

        fused: dict[str, RetrievedChunk] = {}
        for cid, chunk in by_id.items():
            vs, bs = vec_scores.get(cid, 0.0), bm_scores.get(cid, 0.0)
            trust = chunk.meta.confidence if chunk.meta else 1.0
            score = (self.vec_w * vs + self.bm25_w * bs) * trust
            fused[cid] = RetrievedChunk(chunk=chunk, score=score, vector_score=vs,
                                        bm25_score=bs, raw_score=raw_scores.get(cid, 0.0))
        return fused

    async def retrieve(self, query: str) -> list[RetrievedChunk]:
        fused = await self._fuse(query)

        if self.multi_hop and self.reasoner and fused:
            for hop_query in await self._followups(query, list(fused.values())):
                for cid, rc in (await self._fuse(hop_query)).items():
                    if cid not in fused or rc.score > fused[cid].score:
                        fused[cid] = rc

        results = sorted(fused.values(), key=lambda r: r.score, reverse=True)
        candidates = results[: max(self.final_k * 2, self.final_k)]

        if self.do_rerank and self.reasoner and candidates:
            candidates = await self._rerank(query, candidates)

        final = candidates[: self.final_k]
        log.info(
            "retrieval.done",
            query=query[:80],
            fused=len(fused),
            returned=len(final),
            top_score=round(final[0].score, 3) if final else 0.0,
        )
        return final

    async def _followups(self, query: str, top: list[RetrievedChunk]) -> list[str]:
        """Multi-hop: propose 1 follow-up query to fill gaps the top hits imply."""
        context = "\n".join(f"- {r.chunk.text[:160]}" for r in top[:3])
        msg = [
            ChatMessage(
                "system",
                "You expand search. Given a user question and top snippets, output up to "
                "1 additional search query (JSON array of strings) that would retrieve "
                "missing but relevant facts. If none needed, output [].",
            ),
            ChatMessage("user", f"Question: {query}\n\nSnippets:\n{context}"),
        ]
        try:
            raw = await self.reasoner.complete_json(msg, max_tokens=120)
            arr = extract_json(raw, want="array") or []
            return [q for q in arr if isinstance(q, str)][: self.max_hops - 1]
        except Exception:  # noqa: BLE001
            return []

    async def _rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """LLM cross-encoder-style rerank: score each candidate 0..1 for how
        directly it answers the query, then reorder."""
        listing = "\n".join(
            f"[{i}] {r.chunk.text[:400]}" for i, r in enumerate(candidates)
        )
        msg = [
            ChatMessage(
                "system",
                "Score how well each snippet answers the question. Return a JSON object "
                'mapping index (string) to a 0..1 relevance score, e.g. {"0":0.9,"1":0.2}. '
                "Only JSON.",
            ),
            ChatMessage("user", f"Question: {query}\n\nSnippets:\n{listing}"),
        ]
        try:
            raw = await self.reasoner.complete_json(msg, max_tokens=300)
            scores = extract_json(raw)
            # Only stamp rerank scores when the LLM actually produced them —
            # a failed parse must leave rerank_score=None, never a fake 0.0
            # (the confidence gate treats rerank_score as an absolute signal).
            if isinstance(scores, dict) and scores:
                for i, rc in enumerate(candidates):
                    rc.rerank_score = float(scores.get(str(i), 0.0))
                # Blend original fused score with rerank to stay robust to LLM noise.
                candidates.sort(
                    key=lambda r: 0.5 * (r.rerank_score or 0.0) + 0.5 * r.score, reverse=True
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("retrieval.rerank_failed", error=str(exc))
        return candidates
