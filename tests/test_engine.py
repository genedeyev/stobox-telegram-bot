"""End-to-end engine test (offline, stub LLM).

Verifies the full pipeline wires up and returns a response with the expected
shape, citations from the seed docs, and a decision-log entry — without any
external services.
"""

from __future__ import annotations

import pytest

from stobox_ai.core.engine import AgentEngine
from stobox_ai.core.types import Author, ChatType, IncomingMessage


def _msg(text: str, private: bool = True) -> IncomingMessage:
    return IncomingMessage(
        author=Author(external_id="42", display_name="Tester"),
        text=text,
        chat_id="chat-1",
        chat_type=ChatType.PRIVATE if private else ChatType.GROUP,
        message_id="1",
        raw={"addressed": True},
    )


@pytest.mark.asyncio
async def test_engine_answers_and_logs(config):
    engine = await AgentEngine.create(config)
    # Seed docs are indexed on create.
    assert await engine.retriever.store.count() > 0

    resp = await engine.handle(_msg("What is the STBU token used for?"))
    assert resp is not None
    assert resp.text
    # Retrieval should have surfaced the STBU doc as a citation.
    assert any("STBU" in c.title for c in resp.citations)
    # A decision was recorded.
    assert engine.decisions.snapshot()["count"] >= 1


@pytest.mark.asyncio
async def test_engine_stays_silent_on_group_chitchat(config):
    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="7", display_name="Chatter"),
        text="lol nice one",
        chat_id="grp-1",
        chat_type=ChatType.GROUP,
        message_id="9",
        raw={"addressed": False},  # not mentioned, not a doc question
    )
    resp = await engine.handle(msg)
    # Engine should decline to reply to unaddressed group small-talk.
    assert resp is None


@pytest.mark.asyncio
async def test_engine_answers_untagged_group_question(config):
    """A real question in a group is answered even without an @mention/reply."""
    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="8", display_name="Curious"),
        text="What is the STBU token used for?",
        chat_id="grp-2",
        chat_type=ChatType.GROUP,
        message_id="10",
        raw={"addressed": False},  # NOT tagged, NOT a reply
    )
    resp = await engine.handle(msg)
    assert resp is not None and resp.text


@pytest.mark.asyncio
async def test_engine_engages_untagged_fud_to_calm(config):
    """Untagged FUD about Stobox is engaged (to calm/correct), not ignored."""
    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="9", display_name="Skeptic"),
        text="honestly Stobox is a total scam, is this a rugpull?",
        chat_id="grp-3",
        chat_type=ChatType.GROUP,
        message_id="11",
        raw={"addressed": False},
    )
    resp = await engine.handle(msg)
    assert resp is not None and resp.text


@pytest.mark.asyncio
async def test_should_engage_calms_relevant_fud_without_question(config):
    """A non-question FUD statement about Stobox engages via the sentiment clause."""
    from stobox_ai.agents.router import Routing

    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="1", display_name="X"),
        text="stobox rug incoming",
        chat_id="g", chat_type=ChatType.GROUP, message_id="1",
        raw={"addressed": False},
    )
    # Not a question, no docs needed, but FUD about a Stobox topic → engage to calm.
    r = Routing(is_question=False, needs_docs=False, topics=["stobox"], sentiment="fud")
    assert engine._should_engage(msg, r) is True
    # Same heat but NOT about Stobox (no topics) → stay out of it.
    r2 = Routing(is_question=False, needs_docs=False, topics=[], sentiment="angry")
    assert engine._should_engage(msg, r2) is False


@pytest.mark.asyncio
async def test_fud_spike_raises_admin_alert(config):
    """Three FUD messages in a group trip the alert meta on the third."""
    engine = await AgentEngine.create(config)
    engine.fud_alarm.threshold = 3      # explicit

    def fud_msg(i):
        return IncomingMessage(
            author=Author(external_id=f"u{i}", display_name=f"U{i}"),
            text="honestly this looks like a total scam and a rugpull",
            chat_id="grp-fud", chat_type=ChatType.GROUP, message_id=str(i),
            raw={"addressed": False},
        )

    r1 = await engine.handle(fud_msg(1))
    r2 = await engine.handle(fud_msg(2))
    r3 = await engine.handle(fud_msg(3))
    assert not (r1 and r1.meta.get("fud_alert"))
    assert not (r2 and r2.meta.get("fud_alert"))
    assert r3 is not None and r3.meta.get("fud_alert") == 3
