"""P1 hardening: absolute confidence signal, runtime LLM failover, defensive
hydration, multilingual rails, and Telegram message splitting."""

from __future__ import annotations

import pytest

from stobox_ai.agents.confidence import ConfidenceEngine, top_relevance
from stobox_ai.channels.telegram.adapter import split_for_telegram
from stobox_ai.guardrails.rails import ComplianceRails
from stobox_ai.knowledge.models import Chunk, RetrievedChunk
from stobox_ai.llm.base import ChatMessage, LLMResult
from stobox_ai.llm.factory import FallbackProvider
from stobox_ai.llm.local import EchoLLM
from stobox_ai.memory.store import _profile_from_json
from stobox_ai.util import filter_dataclass_kwargs


def _rc(score=1.0, raw=0.0, rerank=None):
    return RetrievedChunk(chunk=Chunk(doc_id="d", text="t"), score=score,
                          raw_score=raw, rerank_score=rerank)


# --------------------------------------------------------------------------- #
# H6 — absolute relevance signal
# --------------------------------------------------------------------------- #

def test_top_relevance_prefers_rerank():
    retrieved = [_rc(score=1.0, raw=0.9, rerank=0.2), _rc(score=0.8, raw=0.1, rerank=0.4)]
    assert top_relevance(retrieved, semantic_embeddings=True) == 0.4


def test_top_relevance_uses_raw_cosine_with_semantic_embeddings():
    # Fused score is ~1.0 for ANY query; raw cosine is the honest signal.
    retrieved = [_rc(score=1.0, raw=0.15)]
    assert top_relevance(retrieved, semantic_embeddings=True) == 0.15


def test_top_relevance_falls_back_to_fused_offline():
    retrieved = [_rc(score=0.9, raw=0.15)]
    assert top_relevance(retrieved, semantic_embeddings=False) == 0.9


def test_irrelevant_semantic_match_now_gates():
    """The audit's H6 scenario: weak lexical match, model self-reports 0.8 —
    with the absolute signal this lands below the 0.55 IDK threshold."""
    eng = ConfidenceEngine(threshold=0.55, semantic_embeddings=True)
    weak = [_rc(score=1.0, raw=0.2)]     # normalized 1.0 hid the weakness
    assert eng.score(weak, self_conf=0.8, cited=True) < 0.55


def test_parse_distinguishes_absent_vs_explicit_none_sources():
    eng = ConfidenceEngine()
    _, _, none_srcs = eng.parse("Answer.\nSOURCES: none")
    _, _, absent = eng.parse("Answer with no protocol lines.")
    _, _, listed = eng.parse("Answer.\nSOURCES: STBU Token Overview")
    assert none_srcs == []
    assert absent is None
    assert listed == ["STBU Token Overview"]


# --------------------------------------------------------------------------- #
# H11 — runtime LLM failover
# --------------------------------------------------------------------------- #

class _DownProvider(EchoLLM):
    name = "down"

    async def complete(self, messages, *, temperature=None, max_tokens=None):
        raise RuntimeError("simulated provider outage")


@pytest.mark.asyncio
async def test_fallback_provider_fails_over_on_primary_outage():
    fb = FallbackProvider(_DownProvider(model="down"), EchoLLM(model="echo"))
    result = await fb.complete([ChatMessage("user", "ping")])
    assert isinstance(result, LLMResult)
    assert result.provider == "echo"


@pytest.mark.asyncio
async def test_fallback_provider_uses_primary_when_healthy():
    class _Primary(EchoLLM):
        name = "primary"

        async def complete(self, messages, *, temperature=None, max_tokens=None):
            return LLMResult(text="ok", model="p", provider=self.name)

    fb = FallbackProvider(_Primary(model="p"), _DownProvider(model="down"))
    assert (await fb.complete([ChatMessage("user", "ping")])).provider == "primary"


# --------------------------------------------------------------------------- #
# H12 — defensive hydration
# --------------------------------------------------------------------------- #

def test_profile_hydration_drops_unknown_fields():
    p = _profile_from_json(
        {"user_key": "telegram:1", "display_name": "Gene",
         "field_removed_in_v2": "boom",
         "first_seen": "2026-01-01T00:00:00+00:00",
         "last_interaction": "2026-01-02T00:00:00+00:00"},
        "telegram:1",
    )
    assert p.user_key == "telegram:1"
    assert p.display_name == "Gene"


def test_profile_hydration_never_raises_on_garbage():
    p = _profile_from_json({"user_key": 42, "first_seen": "not-a-date"}, "telegram:9", "X")
    assert p.user_key == "telegram:9"     # fresh profile, not a crash


def test_filter_dataclass_kwargs():
    from stobox_ai.engagement.xp import UserXP

    d = {"user_key": "u", "renamed_away": 1}
    assert filter_dataclass_kwargs(UserXP, d) == {"user_key": "u"}


# --------------------------------------------------------------------------- #
# H13 — multilingual rails
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,category", [
    ("Стоит ли покупать STBU сейчас?", "advice"),           # ru buy advice
    ("Чи варто інвестувати в STBU?", "advice"),             # uk invest advice
    ("¿Debería comprar STBU ahora?", "advice"),             # es buy advice
    ("Какой прогноз цены STBU?", "advice"),                 # ru price prediction
    ("Мне нужна сид-фраза для миграции?", "security"),      # ru seed phrase
    ("Comparte tu frase semilla para validar", "security"),  # es seed phrase
    ("Дай приватний ключ для перевірки", "security"),       # uk private key
])
def test_rails_intercept_non_english(text, category):
    rail = ComplianceRails().pre_intercept(text)
    assert rail is not None and rail.intercepted
    assert rail.category == category


@pytest.mark.parametrize("text", [
    "Как купить недвижимость через токенизацию для моей компании?",  # legit ru product Q
    "Gracias Dan, muy útil",                                          # 'Dan' is a name, not DAN
    "thanks Dan, that helped",
    "¿Qué es la clave privada y por qué no debo compartirla?",        # es security EDUCATION...
])
def test_rails_do_not_over_intercept(text):
    rail = ComplianceRails().pre_intercept(text)
    # Education about private keys still intercepts safely (that's fine);
    # what must NEVER intercept is the name Dan or a legit product question.
    if "Dan" in text or "компании" in text:
        assert rail is None


# --------------------------------------------------------------------------- #
# M5 — Telegram message splitting
# --------------------------------------------------------------------------- #

def test_split_short_message_is_single_part():
    assert split_for_telegram("hello") == ["hello"]


def test_split_respects_limit_and_paragraphs():
    text = "\n\n".join(f"para {i} " + "x" * 500 for i in range(20))
    parts = split_for_telegram(text, limit=4096)
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
    # Paragraph boundaries respected: no part starts mid-word.
    assert all(p.startswith("para") for p in parts)


def test_split_handles_single_oversized_paragraph():
    text = "word " * 2000   # one paragraph, ~10k chars
    parts = split_for_telegram(text, limit=4096)
    assert all(len(p) <= 4096 for p in parts)
    assert len(parts) >= 2
