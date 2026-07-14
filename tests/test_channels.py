"""Channel-agnostic proof: one engine, multiple transports (offline)."""

from __future__ import annotations

import pytest

from stobox_ai.core.engine import AgentEngine
from stobox_ai.core.types import Author, ChatType, IncomingMessage


@pytest.mark.asyncio
async def test_web_channel_reuses_engine_and_serializes(config):
    from stobox_ai.channels.web import WebChannel

    engine = await AgentEngine.create(config)
    web = WebChannel(engine)

    result = await web.chat(user_id="u1", text="What is the STBU token used for?")
    assert result["answered"] is True
    assert isinstance(result["reply"], str) and result["reply"]
    # Same RAG path → same cited sources, now serialized as JSON.
    assert any("STBU" in c["title"] for c in result["citations"])
    assert result["confidence"] in ("high", "medium", "low")


@pytest.mark.asyncio
async def test_same_engine_serves_two_channels(config):
    """A single engine instance handles both a 'telegram' and a 'web' message."""
    engine = await AgentEngine.create(config)

    def msg(channel: str, uid: str) -> IncomingMessage:
        return IncomingMessage(
            author=Author(external_id=uid, channel=channel),
            text="What is ERC-3643?",
            chat_id=f"{channel}-chat",
            chat_type=ChatType.PRIVATE,
            message_id="1",
            channel=channel,
            raw={"addressed": True},
        )

    tg = await engine.handle(msg("telegram", "tg-user"))
    web = await engine.handle(msg("web", "web-user"))
    assert tg is not None and web is not None
    # Both channels get answers from the same knowledge base.
    assert any("ERC-3643" in c.title for c in tg.citations)
    assert any("ERC-3643" in c.title for c in web.citations)
    # Decisions were logged for both channels.
    assert engine.decisions.snapshot()["count"] >= 2


def test_discord_adapter_imports_without_sdk():
    # Module must import even when discord.py isn't installed (lazy import).
    from stobox_ai.channels.discord import DiscordChannel

    assert DiscordChannel.name == "discord"
