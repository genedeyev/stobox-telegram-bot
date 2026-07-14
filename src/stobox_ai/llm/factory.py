"""Build provider instances from config + secrets.

Central place that maps ``config.llm.*`` to concrete providers. Falls back to
offline local providers when keys are absent so the app is always runnable.
"""

from __future__ import annotations

from ..config import Config, get_secrets
from ..logging import get_logger
from .base import EmbeddingProvider, LLMProvider
from .local import EchoLLM, LocalHashEmbeddings

log = get_logger(__name__)


def _reasoner(provider: str, model: str, temperature: float, max_tokens: int) -> LLMProvider | None:
    secrets = get_secrets()
    try:
        if provider == "anthropic" and secrets.anthropic_key:
            from .anthropic_provider import AnthropicProvider

            return AnthropicProvider(model, secrets.anthropic_key, temperature, max_tokens)
        if provider == "openai" and secrets.openai_key:
            from .openai_provider import OpenAIProvider

            return OpenAIProvider(model, secrets.openai_key, temperature, max_tokens)
    except ImportError as exc:
        # SDK for the configured provider isn't installed — degrade instead of crash.
        log.error("reasoner.sdk_missing", provider=provider, error=str(exc))
    return None


def build_reasoner(config: Config) -> LLMProvider:
    r = config.section("llm.reasoning")
    primary = _reasoner(
        r.get("provider", "anthropic"),
        r.get("model", "claude-opus-4-8"),
        float(r.get("temperature", 0.3)),
        int(r.get("max_tokens", 1200)),
    )
    if primary:
        return primary
    # Try configured fallback provider before giving up on real reasoning.
    fb = _reasoner(
        r.get("fallback_provider", "openai"),
        r.get("fallback_model", "gpt-4.1"),
        float(r.get("temperature", 0.3)),
        int(r.get("max_tokens", 1200)),
    )
    if fb:
        log.warning("reasoner.fallback", provider=fb.name)
        return fb
    log.warning("reasoner.offline_stub", reason="no API keys configured")
    return EchoLLM(model="echo")


def build_classifier(config: Config) -> LLMProvider:
    c = config.section("llm.classifier")
    provider = _reasoner(
        c.get("provider", "anthropic"),
        c.get("model", "claude-haiku-4-5-20251001"),
        float(c.get("temperature", 0.0)),
        512,
    )
    return provider or build_reasoner(config)


def build_embedder(config: Config) -> EmbeddingProvider:
    e = config.section("llm.embeddings")
    dims = int(e.get("dimensions", 1024))
    secrets = get_secrets()
    if e.get("provider", "openai") == "openai" and secrets.openai_key:
        try:
            from .openai_provider import OpenAIEmbeddings

            return OpenAIEmbeddings(e.get("model", "text-embedding-3-large"), dims, secrets.openai_key)
        except ImportError as exc:
            log.error("embedder.sdk_missing", error=str(exc))
    log.warning("embedder.offline_hash", reason="no OpenAI key; using local hash embeddings")
    return LocalHashEmbeddings(model="local-hash", dimensions=dims)
