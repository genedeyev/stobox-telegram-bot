"""Provider-abstracted LLM + embeddings layer.

Reasoning defaults to Anthropic; OpenAI is a drop-in swap. Embeddings default
to OpenAI (they feed pgvector). Nothing above this package imports a vendor SDK
directly — they go through :class:`LLMProvider` / :class:`EmbeddingProvider`.
"""

from .base import ChatMessage, EmbeddingProvider, LLMProvider, LLMResult
from .factory import build_classifier, build_embedder, build_reasoner

__all__ = [
    "ChatMessage",
    "LLMProvider",
    "LLMResult",
    "EmbeddingProvider",
    "build_reasoner",
    "build_classifier",
    "build_embedder",
]
