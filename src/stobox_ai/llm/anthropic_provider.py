"""Anthropic Claude reasoning provider (the default reasoner)."""

from __future__ import annotations

import time

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..logging import get_logger
from .base import ChatMessage, LLMProvider, LLMResult

log = get_logger(__name__)


def _retryable(exc: BaseException) -> bool:
    """Retry transient failures only — 4xx request errors are handled inline."""
    from anthropic import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code >= 500


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, temperature: float = 0.3, max_tokens: int = 1200):
        super().__init__(model, temperature, max_tokens)
        # Imported lazily so the package imports without the SDK installed.
        from anthropic import AsyncAnthropic

        # Explicit request timeout (SDK default is 600s — a hung call must not
        # stall the bot) and max_retries=0: tenacity owns the retry policy.
        self._client = AsyncAnthropic(api_key=api_key, timeout=45.0, max_retries=0)
        # Newer models (e.g. claude-opus-4-8) reject `temperature` as deprecated.
        # Learned at runtime on the first 400 and remembered per provider.
        self._no_temperature = False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        retry=retry_if_exception(_retryable),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        from anthropic import BadRequestError

        system = "\n\n".join(m.content for m in messages if m.role == "system")
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        kwargs: dict = {
            "model": self.model,
            "messages": convo,
            "max_tokens": max_tokens or self.max_tokens,
        }
        # Only send `system` when present — system=None is a 400 on the API.
        if system:
            kwargs["system"] = system
        if not self._no_temperature:
            kwargs["temperature"] = self.temperature if temperature is None else temperature

        started = time.perf_counter()
        try:
            resp = await self._client.messages.create(**kwargs)
        except BadRequestError as exc:
            # Model rejects temperature (deprecated) → drop it and remember.
            if "temperature" in str(exc) and "temperature" in kwargs:
                log.info("anthropic.temperature_unsupported", model=self.model)
                self._no_temperature = True
                kwargs.pop("temperature")
                resp = await self._client.messages.create(**kwargs)
            else:
                raise
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
