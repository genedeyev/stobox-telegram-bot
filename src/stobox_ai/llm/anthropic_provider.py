"""Anthropic Claude reasoning provider (the default reasoner)."""

from __future__ import annotations

import time

from tenacity import retry, stop_after_attempt, wait_exponential

from .base import ChatMessage, LLMProvider, LLMResult


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, temperature: float = 0.3, max_tokens: int = 1200):
        super().__init__(model, temperature, max_tokens)
        # Imported lazily so the package imports without the SDK installed.
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        system = "\n\n".join(m.content for m in messages if m.role == "system") or None
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        started = time.perf_counter()
        resp = await self._client.messages.create(
            model=self.model,
            system=system,
            messages=convo,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        elapsed = (time.perf_counter() - started) * 1000
        text = "".join(block.text for block in resp.content if block.type == "text")
        return LLMResult(
            text=text.strip(),
            model=self.model,
            provider=self.name,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            latency_ms=elapsed,
        )
