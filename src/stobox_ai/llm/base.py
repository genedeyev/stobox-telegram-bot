"""LLM + embedding provider interfaces.

Keep these tiny and vendor-neutral. A new provider = one class implementing
``complete``/``embed``. The rest of the platform is unaffected.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    provider: str = ""
    meta: dict = field(default_factory=dict)


class LLMProvider(abc.ABC):
    """Text-in / text-out reasoning provider."""

    name: str = "base"

    def __init__(self, model: str, temperature: float = 0.3, max_tokens: int = 1200) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        ...

    async def complete_json(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> str:
        """Convenience: complete at temperature 0 for structured/JSON output."""
        res = await self.complete(messages, temperature=0.0, max_tokens=max_tokens)
        return res.text


class EmbeddingProvider(abc.ABC):
    name: str = "base"

    def __init__(self, model: str, dimensions: int) -> None:
        self.model = model
        self.dimensions = dimensions

    @abc.abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]
