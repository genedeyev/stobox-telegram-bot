"""Offline/deterministic providers for local dev, tests, and CI.

No network, no API keys. The hash embedder is NOT semantically meaningful — it
exists so the vector store, retrieval, and pipeline are runnable and testable
without external services. Production uses real providers via the factory.
"""

from __future__ import annotations

import hashlib

from .base import ChatMessage, EmbeddingProvider, LLMProvider, LLMResult


class LocalHashEmbeddings(EmbeddingProvider):
    """Deterministic pseudo-embeddings from token hashing. Offline only."""

    name = "local-hash"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        dims = self.dimensions
        acc = [0.0] * dims
        for token in text.lower().split():
            h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
            acc[h % dims] += 1.0
        norm = sum(v * v for v in acc) ** 0.5 or 1.0
        return [v / norm for v in acc]


class EchoLLM(LLMProvider):
    """A stub reasoner used only when no API keys are configured, so the app
    boots and the wiring is demonstrable. Returns a clearly-labeled canned
    reply. Never use in production."""

    name = "echo"

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResult(
            text=f"[echo-llm — no API key configured] I received: {user[:200]}",
            model="echo",
            provider=self.name,
        )
