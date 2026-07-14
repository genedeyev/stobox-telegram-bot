"""Discord adapter — the same engine on a third transport.

Mirrors the Telegram adapter: Discord message → :class:`IncomingMessage` →
shared engine → rendered reply + moderation. discord.py is imported lazily so it
stays an optional dependency; the module imports fine without it.

Run with DISCORD_BOT_TOKEN set. Enable the "Message Content" privileged intent
in the Discord developer portal.
"""

from __future__ import annotations

import os
import re

from ...core.engine import AgentEngine
from ...core.types import (
    Author,
    ChatType,
    IncomingMessage,
    ModerationAction,
)
from ...logging import get_logger
from ..base import Channel

log = get_logger(__name__)
_URL = re.compile(r"https?://\S+")


class DiscordChannel(Channel):
    name = "discord"

    def __init__(self, engine: AgentEngine, token: str | None = None) -> None:
        super().__init__(engine)
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN")
        self.admins = {
            x for x in os.environ.get("DISCORD_ADMIN_USER_IDS", "").replace(" ", "").split(",") if x
        }
        self.client = None
        self._bot_id: str | None = None

    async def start(self) -> None:  # pragma: no cover - needs live Discord
        import discord

        if not self.token:
            raise RuntimeError("DISCORD_BOT_TOKEN is not set")

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        adapter = self

        @client.event
        async def on_ready() -> None:
            adapter._bot_id = str(client.user.id)
            log.info("discord.start", user=str(client.user))

        @client.event
        async def on_message(message) -> None:
            if message.author.bot:
                return
            await adapter._on_message(client, message)

        self.client = client
        await client.start(self.token)

    async def stop(self) -> None:  # pragma: no cover
        if self.client:
            await self.client.close()

    async def _on_message(self, client, message) -> None:  # pragma: no cover - live only
        incoming = self._to_incoming(client, message)
        try:
            response = await self.engine.handle(incoming)
        except Exception as exc:  # noqa: BLE001
            log.error("discord.handle_failed", error=str(exc))
            return
        if response is None:
            return
        if response.moderation != ModerationAction.NONE:
            await self._apply_moderation(message, response)
        if response.should_reply:
            footer = self.render_citations(response)
            await message.reply((response.text + footer)[:2000])

    async def _apply_moderation(self, message, response) -> None:  # pragma: no cover
        import datetime

        try:
            if response.moderation == ModerationAction.DELETE:
                await message.delete()
            elif response.moderation == ModerationAction.MUTE:
                mins = int(self.engine.config.get("moderation.mute_minutes", 60))
                until = datetime.timedelta(minutes=mins)
                await message.author.timeout(until)
            elif response.moderation == ModerationAction.BAN:
                await message.delete()
                await message.guild.ban(message.author, reason="Stobox moderation: scam/abuse")
            log.info("discord.moderation_applied", action=response.moderation.value)
        except Exception as exc:  # noqa: BLE001 - missing perms, DMs, etc.
            log.warning("discord.moderation_failed", error=str(exc))

    def _to_incoming(self, client, message) -> IncomingMessage:
        is_dm = message.guild is None
        text = message.content or ""
        addressed = bool(client.user and client.user in getattr(message, "mentions", []))
        if message.reference and message.reference.resolved is not None:
            ref = message.reference.resolved
            addressed = addressed or (getattr(ref, "author", None) == client.user)
        return IncomingMessage(
            author=Author(
                external_id=str(message.author.id),
                channel="discord",
                username=getattr(message.author, "name", None),
                display_name=getattr(message.author, "display_name", None),
                is_admin=str(message.author.id) in self.admins,
            ),
            text=text,
            chat_id=str(message.channel.id),
            chat_type=ChatType.PRIVATE if is_dm else ChatType.GROUP,
            message_id=str(message.id),
            channel="discord",
            reply_to_text=None,
            links=_URL.findall(text),
            raw={"addressed": addressed or is_dm},
        )
