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
