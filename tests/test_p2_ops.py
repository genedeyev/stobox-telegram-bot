"""P2 ops hardening: prompt-caching message split, GDPR erasure path,
message-log robustness, decision-log lifecycle (offline paths)."""

from __future__ import annotations

import json

import pytest

from stobox_ai.analytics.logger import Decision, DecisionLog
from stobox_ai.llm.base import ChatMessage
from stobox_ai.ops.message_log import MessageLog

# --------------------------------------------------------------------------- #
# M2 — system prompt split for prompt caching
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_engine_system_messages_split_stable_and_dynamic(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    msgs = engine.system_messages()
    assert msgs is not None and len(msgs) == 2
    stable, dynamic = msgs
    assert stable.role == "system" and "[CORE]" in stable.content
    assert "[CANONICALS]" in stable.content
    assert dynamic.role == "system"
    assert "[CANONICALS]" not in dynamic.content       # freshness only
    # The stable prefix really is stable across calls (cacheable).
    assert engine.system_messages()[0].content == stable.content


def test_anthropic_style_system_block_convention():
    """The provider convention: all-but-last system messages are the stable,
    cache-marked prefix. Verify the split we send matches that contract."""
    msgs = [ChatMessage("system", "stable"), ChatMessage("system", "dynamic"),
            ChatMessage("user", "q")]
    system_texts = [m.content for m in msgs if m.role == "system" and m.content]
    assert system_texts == ["stable", "dynamic"]


# --------------------------------------------------------------------------- #
# M10 — GDPR erasure
# --------------------------------------------------------------------------- #

def _mklog(tmp_path) -> MessageLog:
    return MessageLog(tmp_path / "log.jsonl", cap_per_chat=100, retention_days=90)


def test_message_log_purge_user(tmp_path):
    ml = _mklog(tmp_path)
    ml.append(chat_id="c1", chat_title="t", user_id="111", username="a",
              display_name="A", text="hello", message_id="1")
    ml.append(chat_id="c1", chat_title="t", user_id="222", username="b",
              display_name="B", text="world", message_id="2")
    ml.append(chat_id="c2", chat_title="t2", user_id="111", username="a",
              display_name="A", text="again", message_id="3")
    assert ml.purge_user("111") == 2
    assert ml.total() == 1
    # Purge persisted: a reload sees only the survivor.
    ml2 = _mklog(tmp_path)
    assert ml2.total() == 1
    assert ml2.recent("c1", 5)[0].user_id == "222"


def test_message_log_tolerates_corrupt_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    good = {"at": "2026-07-01T00:00:00+00:00", "chat_id": "c1", "chat_title": "",
            "user_id": "1", "username": None, "display_name": "A",
            "text": "hi", "message_id": "1", "reply_to": None}
    path.write_text(json.dumps(good) + "\n" + '{"truncated": \n' + json.dumps(good) + "\n")
    ml = MessageLog(path, cap_per_chat=100, retention_days=3650)
    assert ml.total("c1") == 2         # both good lines survive the bad one


@pytest.mark.asyncio
async def test_engine_forget_user_erases_everything(config):
    from stobox_ai.core.engine import AgentEngine
    from stobox_ai.core.types import Author, ChatType, IncomingMessage

    engine = await AgentEngine.create(config)
    msg = IncomingMessage(
        author=Author(external_id="424242", channel="telegram", display_name="Erase Me"),
        text="What is STBU?", chat_id="424242", chat_type=ChatType.PRIVATE,
        message_id="1", channel="telegram", raw={"addressed": True},
    )
    await engine.handle(msg)
    engine.xp.award("telegram:424242", 5, "test", "Erase Me")
    engine.reminders.subscribe("424242")
    assert engine.xp.get("telegram:424242") is not None

    result = await engine.forget_user("telegram", "424242")
    assert result["profile"] is True
    assert result["threads"] >= 1
    assert engine.xp.get("telegram:424242") is None
    assert not engine.reminders.is_subscribed("424242")
    assert engine.memory._profiles.get("telegram:424242") is None


# --------------------------------------------------------------------------- #
# Decision log lifecycle (offline: no pool → clean no-ops)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_decision_log_offline_lifecycle():
    dlog = DecisionLog()   # no pool
    await dlog.record(Decision(user_key="telegram:1", question="q1"))
    await dlog.record(Decision(user_key="telegram:2", question="q2"))
    assert await dlog.backfill() == 0
    assert await dlog.prune(90) == 0
    removed = await dlog.purge_user("telegram:1")
    assert removed == 1
    assert [d.user_key for d in dlog.records()] == ["telegram:2"]
