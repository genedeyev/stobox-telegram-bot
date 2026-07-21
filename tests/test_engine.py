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
async def test_group_history_attributes_distinct_speakers(config):
    """In a shared group thread, history labels each user turn by name so two
    different people are never blended into one identity (the DhCrypto≠Gene bug)."""
    engine = await AgentEngine.create(config)
    tk = "telegram:grp-9:main"
    engine.memory.add_turn(tk, "user", "gm everyone", name="Gene")
    engine.memory.add_turn(tk, "assistant", "Morning, Gene!")
    engine.memory.add_turn(tk, "user", "flag that answer", name="DhCrypto")
    engine.memory.add_turn(tk, "user", "(current turn)", name="DhCrypto")  # excluded
    hist = engine._format_history(tk)
    assert "User (Gene): gm everyone" in hist
    assert "User (DhCrypto): flag that answer" in hist
    assert "You: Morning, Gene!" in hist        # assistant turns stay unnamed


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
async def test_engine_stays_silent_on_untagged_group_question(config):
    """COEXIST: in a group Stoby only replies when ADDRESSED. An untagged
    question is left alone (Arevik's rule: don't answer every message). The
    same question, addressed, IS answered."""
    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="8", display_name="Curious"),
        text="What is the STBU token used for?",
        chat_id="grp-2", chat_type=ChatType.GROUP, message_id="10",
        raw={"addressed": False},  # NOT tagged, NOT a reply
    )
    assert await engine.handle(msg) is None
    msg.raw["addressed"] = True
    resp = await engine.handle(msg)
    assert resp is not None and resp.text


@pytest.mark.asyncio
async def test_engine_ignores_untagged_fud_by_default(config):
    """Untagged FUD is no longer auto-engaged — Stoby doesn't wade in unasked.
    (A coordinated FUD SPIKE still alerts admins via the separate FUD alarm.)"""
    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="9", display_name="Skeptic"),
        text="honestly Stobox is a total scam, is this a rugpull?",
        chat_id="grp-3", chat_type=ChatType.GROUP, message_id="11",
        raw={"addressed": False},
    )
    assert await engine.handle(msg) is None      # no public reply


@pytest.mark.asyncio
async def test_should_engage_answers_questions_not_chatter(config):
    """Arevik's rule: answer a question (tagged OR clear unaddressed Stobox
    question), but never react to greetings, FUD, or general chatter."""
    from stobox_ai.agents.router import Routing

    engine = await AgentEngine.create(config)   # config default: answer questions ON

    def _m(addressed, text="stobox rug incoming"):
        return IncomingMessage(
            author=Author(external_id="1", display_name="X"), text=text,
            chat_id="g", chat_type=ChatType.GROUP, message_id="1",
            raw={"addressed": addressed})

    fud = Routing(is_question=False, needs_docs=False, topics=["stobox"], sentiment="fud")
    question = Routing(is_question=True, needs_docs=True, topics=["stbu"], sentiment="neutral")
    q_msg = _m(False, "how does the STBU migration work?")
    # Addressed → always engage.
    assert engine._should_engage(_m(True), fud) is True
    # Unaddressed FUD / chatter → silent (no auto-calm, no reacting to everything).
    assert engine._should_engage(_m(False), fud) is False
    # Unaddressed but a CLEAR Stobox question → engage ("when it sees a question").
    assert engine._should_engage(q_msg, question) is True
    # Turning the flag off makes even questions addressed-only.
    engine.config.raw["engagement"]["answer_unaddressed_questions"] = False
    try:
        assert engine._should_engage(q_msg, question) is False
    finally:
        engine.config.raw["engagement"]["answer_unaddressed_questions"] = True


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


@pytest.mark.asyncio
async def test_mql_summary_emitted_once(config):
    """An MQL (email + intent) sets mql_summary the first time only."""
    engine = await AgentEngine.create(config)

    def msg():
        return IncomingMessage(
            author=Author(external_id="mql1", display_name="Lead"),
            text="I want to tokenize my building — email me at lead@acme.com",
            chat_id="dm-1", chat_type=ChatType.PRIVATE, message_id="1",
            raw={"addressed": True},
        )

    r1 = await engine.handle(msg())
    assert r1 is not None and r1.meta.get("mql_summary")        # first time → notify
    assert "lead@acme.com" in r1.meta["mql_summary"]
    r2 = await engine.handle(msg())
    assert r2 is not None and not r2.meta.get("mql_summary")    # already notified


@pytest.mark.asyncio
async def test_admin_impersonator_is_banned(config):
    """A NON-admin whose name copies an admin is banned + deleted (Arevik's
    exception — the one enforcement Stoby keeps in coexist mode)."""
    from stobox_ai.core.types import ModerationAction

    engine = await AgentEngine.create(config)
    m = IncomingMessage(
        author=Author(external_id="99", display_name="Arevik | Support @ Stobox"),
        text="DM me for help with your wallet", chat_id="g", chat_type=ChatType.GROUP,
        message_id="1", raw={"addressed": True},
    )
    r = await engine.handle(m)
    assert r is not None
    assert r.moderation == ModerationAction.BAN
    assert r.meta.get("category") == "admin_impersonation"


@pytest.mark.asyncio
async def test_ordinary_user_answered_without_moderation(config):
    """COEXIST: a normal member asking a question is answered; Stoby takes no
    moderation action on ordinary messages (ChatKeeper handles moderation)."""
    from stobox_ai.core.types import ModerationAction

    engine = await AgentEngine.create(config)
    m = IncomingMessage(
        author=Author(external_id="42", display_name="Curious Member"),
        text="how does the STBU migration work?", chat_id="g", chat_type=ChatType.GROUP,
        message_id="1", raw={"addressed": True},
    )
    r = await engine.handle(m)
    assert r is not None and r.text.strip()
    assert r.moderation == ModerationAction.NONE


def test_is_greeting():
    from stobox_ai.core.engine import _is_greeting
    for t in ["hi", "hey man", "hello there", "gm everyone", "yo", "good morning", "sup"]:
        assert _is_greeting(t), t
    for t in ["history of tokens", "highly volatile", "gmail is down", "what's up with STBU"]:
        assert not _is_greeting(t), t


@pytest.mark.asyncio
async def test_untagged_greeting_stays_silent(config):
    """COEXIST: a bare greeting in a group is NOT replied to (would be spam).
    Stoby greets people via the new-member welcome, not by answering every 'hi'."""
    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="1", display_name="Gene"),
        text="hi", chat_id="g", chat_type=ChatType.GROUP, message_id="1",
        raw={"addressed": False},
    )
    assert await engine.handle(msg) is None


def test_looks_like_question_backstop():
    from stobox_ai.core.engine import _looks_like_question
    assert _looks_like_question("how does migration work?")
    # The real missed message from the group — ends with '?', so it's caught.
    assert _looks_like_question("OK and how we ensure the sources stay in sync?")
    assert _looks_like_question("is this live")                                # opener
    assert _looks_like_question("great, thanks — but what about fees?")        # trailing ?
    assert not _looks_like_question("thanks, that helps")
    assert not _looks_like_question("gm everyone")
    assert not _looks_like_question("I'll show you how we do it")              # mid 'how', no ?


@pytest.mark.asyncio
async def test_addressed_group_message_always_engages(config):
    """When Stoby IS addressed, it engages regardless of routing signals."""
    engine = await AgentEngine.create(config)

    class R:
        is_question = False
        needs_docs = False
        topics = []
        sentiment = "neutral"

    msg = IncomingMessage(
        author=Author(external_id="1", display_name="Arevik"),
        text="Stoby, how do we keep the official sources in sync",
        chat_id="g", chat_type=ChatType.GROUP, message_id="1",
        raw={"addressed": True},
    )
    assert engine._should_engage(msg, R()) is True
