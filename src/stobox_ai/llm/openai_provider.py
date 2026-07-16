"""OpenAI reasoning + embedding providers.

Reasoning here is the drop-in swap for Anthropic. Embeddings are the default
that feeds pgvector.
"""

from __future__ import annotations

import time

from tenacity import retry, stop_after_attempt, wait_exponential

from .base import ChatMessage, EmbeddingProvider, LLMProvider, LLMResult


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str, temperature: float = 0.3, max_tokens: int = 1200):
        super().__init__(model, temperature, max_tokens)
        from openai import AsyncOpenAI

        # Explicit request timeout (SDK default is 600s) and max_retries=0:
        # tenacity owns the retry policy.
        self._client = AsyncOpenAI(api_key=api_key, timeout=45.0, max_retries=0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        elapsed = (time.perf_counter() - started) * 1000
        usage = resp.usage
        return LLMResult(
            text=(resp.choices[0].message.content or "").strip(),
            model=self.model,
            provider=self.name,
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            latency_ms=elapsed,
        )


class OpenAIEmbeddings(EmbeddingProvider):
    name = "openai"

    def __init__(self, model: str, dimensions: int, api_key: str):
        super().__init__(model, dimensions)
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=45.0, max_retries=0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.embeddings.create(
            model=self.model, input=texts, dimensions=self.dimensions
        )
        return [d.embedding for d in resp.data]
