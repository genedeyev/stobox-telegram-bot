"""Proactive Intelligence tests (offline, deterministic)."""

from __future__ import annotations

import pytest

from stobox_ai.analytics.logger import Decision, DecisionLog
from stobox_ai.insights import (
    DailyDigest,
    cluster_questions,
    documentation_gaps,
    potential_leads,
    sentiment_proxy,
)


def _log_with(decisions: list[Decision]) -> DecisionLog:
    dl = DecisionLog()
    dl._ring.extend(decisions)
    return dl


def _d(question, confidence="high", score=0.9, mode="community_manager", **kw) -> Decision:
    return Decision(question=question, confidence=confidence, confidence_score=score, mode=mode, **kw)


def test_cluster_groups_similar_questions():
    decisions = [
        _d("How do I migrate my STBU tokens?"),
        _d("What is the process to migrate STBU tokens?"),
        _d("Tell me about ERC-3643 compliance"),
    ]
    clusters = cluster_questions(decisions)
    # The two migration questions collapse into one cluster; ERC-3643 stays separate.
    assert clusters[0].count == 2
    assert len(clusters) == 2


def test_documentation_gaps_flags_recurring_low_confidence():
    decisions = [
        _d("What is the exact STBU price?", confidence="low", score=0.1),
        _d("Tell me the exact price of STBU today", confidence="low", score=0.1),
        _d("What is Compass?", confidence="high", score=0.9),
    ]
    gaps = documentation_gaps(decisions)
    assert len(gaps) == 1
    assert "price" in gaps[0].representative.lower()
    assert gaps[0].is_gap


def test_potential_leads_ranks_captured_first():
    decisions = [
        _d("pricing?", mode="sales_assistant", user_key="u1"),
        _d("demo please", mode="sales_assistant", user_key="u2", lead_captured=True),
        _d("what is compass", user_key="u3"),
    ]
    leads = potential_leads(decisions)
    assert leads[0]["user_key"] == "u2" and leads[0]["captured"]
    assert {lead["user_key"] for lead in leads} == {"u1", "u2"}


def test_sentiment_proxy_reacts_to_moderation_and_low_conf():
    healthy = [_d("q1"), _d("q2")]
    troubled = [
        _d("q1", confidence="low", score=0.1),
        _d("scam", mode="moderator", moderation="ban", escalated=True),
    ]
    assert sentiment_proxy(healthy)["health_score"] > sentiment_proxy(troubled)["health_score"]


def test_daily_digest_structure_and_render():
    decisions = [
        _d("How do I migrate STBU?"),
        _d("How to migrate STBU tokens?"),
        _d("exact price?", confidence="low", score=0.1),
        _d("exact price of STBU?", confidence="low", score=0.1),
        _d("demo?", mode="sales_assistant", user_key="u9", lead_captured=True),
    ]
    digest = DailyDigest(_log_with(decisions)).build()
    assert digest["count"] == 5
    assert digest["top_questions"]
    assert digest["documentation_gaps"]           # the price cluster
    assert digest["potential_leads"]
    text = DailyDigest.render_text(digest)
    assert "Daily Community Digest" in text
    assert "Documentation gaps" in text


@pytest.mark.asyncio
async def test_weekly_faq_end_to_end(config):
    from stobox_ai.core.engine import AgentEngine
    from stobox_ai.core.types import Author, ChatType, IncomingMessage
    from stobox_ai.llm.base import LLMResult

    engine = await AgentEngine.create(config)

    class ScriptedReasoner:
        name = model = "scripted"
        async def complete(self, messages, temperature=None, max_tokens=None):
            return LLMResult(text="STBU is a utility token used for platform access.",
                             model="scripted", provider="scripted")
        async def complete_json(self, messages, max_tokens=None):
            return "{}"

    engine.reasoner = ScriptedReasoner()
    engine.retriever.reasoner = ScriptedReasoner()

    # Seed the decision log with real questions via the engine.
    for q in ["What is the STBU token?", "Explain the STBU token utility", "What is Compass?"]:
        await engine.handle(IncomingMessage(
            author=Author(external_id="1"), text=q, chat_id="c",
            chat_type=ChatType.PRIVATE, message_id="1", raw={"addressed": True}))

    entries = await engine.weekly_faq().generate(top_n=5)
    assert entries
    answered = [e for e in entries if not e.needs_docs]
    assert answered, "expected at least one answerable FAQ entry"
    assert all(e.frequency >= 1 for e in entries)
