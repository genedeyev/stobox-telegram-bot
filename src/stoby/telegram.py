"""aiogram router. Stoby speaks in a private chat, or in a group when addressed:
mentioned by @username, called by name, or replied to. Nothing else."""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .announce import RSS_URL, parse_rss
from .answer import Pipeline
from .commands import (
    CONTACT,
    HELP,
    KNOWN,
    MENU,
    UNKNOWN,
    blog_text,
    check_text,
    sources_text,
    stbu_text,
)
from .format import is_stbu_topic, links_block, to_html
from .leads import Leads
from .ledger import log
from .sources.chain import ChainReader
from .sources.site import SiteSource, SiteUnavailable

_NAME = re.compile(r"(?i)\b(stoby|stobi|stobbie)\b")
PER_USER_PER_MIN, PER_USER_PER_DAY = 6, 60


def split_for_telegram(text: str, limit: int = 4096) -> list[str]:
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, current = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = para.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            head, para = para[:cut].rstrip(), para[cut:].lstrip()
            if current:
                parts.append(current)
                current = ""
            parts.append(head)
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > limit:
            parts.append(current)
            current = para
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


class RateLimit:
    def __init__(self, per_min: int = PER_USER_PER_MIN, per_day: int = PER_USER_PER_DAY) -> None:
        self.per_min, self.per_day = per_min, per_day
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, user: str, now: float | None = None) -> bool:
        now = now or time.time()
        q = self.hits[user]
        while q and now - q[0] > 86400:
            q.popleft()
        minute = sum(1 for t in q if now - t < 60)
        if minute >= self.per_min or len(q) >= self.per_day:
            return False
        q.append(now)
        return True


class StobyBot:
    def __init__(self, bot: Bot, pipeline: Pipeline, site: SiteSource, chain: ChainReader,
                 leads: Leads, admin_ids: frozenset[int], username: str = "") -> None:
        self.bot, self.pipeline, self.site, self.chain, self.leads = bot, pipeline, site, chain, leads
        self.admins, self.username = admin_ids, username
        self.limit = RateLimit()
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=6))
        self.router = Router()
        self._wire()

    def _wire(self) -> None:
        r = self.router
        r.message(Command("start", "help"))(self.cmd_help)
        r.message(Command("sources"))(self.cmd_sources)
        r.message(Command("contact"))(self.cmd_contact)
        r.message(Command("stbu"))(self.cmd_stbu)
        r.message(Command("check"))(self.cmd_check)
        r.message(Command("blog"))(self.cmd_blog)
        r.message(F.text.startswith("/"))(self.on_other_command)
        r.message(F.text)(self.on_text)

    async def _send(self, message: Message, text: str) -> None:
        """Send Telegram HTML; if Telegram rejects the markup, send it as plain
        text rather than dropping the answer."""
        for part in split_for_telegram(text):
            try:
                await message.reply(part, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except TelegramBadRequest as exc:
                log.warning("send.html_rejected", error=str(exc)[:120])
                plain = re.sub(r"<[^>]+>", "", part).replace("&lt;", "<").replace("&gt;", ">") \
                    .replace("&amp;", "&")
                await message.reply(plain, disable_web_page_preview=True)

    def _mine(self, message: Message) -> bool:
        """ChatKeeper moderates the group and answers the generic commands there.
        In a group, /start /help /sources /contact are Stoby's only when addressed
        to it (/help@stobox_assistant_bot); a bare one is left to ChatKeeper, so the
        two bots never answer the same command (Gene, 28.09.2026: work in parallel)."""
        if message.chat.type == ChatType.PRIVATE:
            return True
        head = (message.text or "").split(maxsplit=1)[0].lower()
        return bool(self.username) and head.endswith("@" + self.username.lower())

    async def cmd_help(self, message: Message) -> None:
        if not self._mine(message):
            return
        await self._send(message, HELP)

    async def cmd_sources(self, message: Message) -> None:
        if not self._mine(message):
            return
        try:
            site = await self.site.facts()
        except SiteUnavailable:
            site = None
        await self._send(message, sources_text(site))

    async def cmd_contact(self, message: Message) -> None:
        if not self._mine(message):
            return
        await self._send(message, CONTACT)

    async def cmd_stbu(self, message: Message) -> None:
        try:
            site = await self.site.facts()
        except SiteUnavailable:
            await self._send(message, "I can't read the published record right now: https://www.stobox.io/stbu")
            return
        await self._send(message, stbu_text(site))

    async def cmd_check(self, message: Message, command: CommandObject) -> None:
        if not self.limit.allow(str(message.from_user.id if message.from_user else 0)):
            return
        try:
            site = await self.site.facts()
        except SiteUnavailable:
            await self._send(message, "I can't read the published record right now: https://www.stobox.io/stbu")
            return
        await self._send(message, await check_text(command.args or "", site, self.chain))

    async def cmd_blog(self, message: Message) -> None:
        import httpx

        posts = []
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get(RSS_URL)
                r.raise_for_status()
                posts = parse_rss(r.text)
        except Exception as exc:  # noqa: BLE001
            log.warning("blog.feed_unreadable", error=type(exc).__name__)
        await self._send(message, blog_text(posts))

    async def on_other_command(self, message: Message) -> None:
        """A command that is not Stoby's never reaches the model. Addressed to
        Stoby (or in a DM): say what Stoby can do. Bare, in the group: it may be
        ChatKeeper's, so stay silent."""
        head = (message.text or "").split(maxsplit=1)[0]
        name, _, bot = head[1:].partition("@")
        if name.lower() in KNOWN:
            return                              # handled by its own router entry
        to_me = message.chat.type == ChatType.PRIVATE or (
            bool(self.username) and bot.lower() == self.username.lower())
        if to_me:
            await self._send(message, UNKNOWN)

    def addressed(self, message: Message) -> bool:
        if message.chat.type == ChatType.PRIVATE:
            return True
        text = message.text or ""
        if self.username and f"@{self.username}".lower() in text.lower():
            return True
        if _NAME.search(text):
            return True
        rep = message.reply_to_message
        return bool(rep and rep.from_user and rep.from_user.is_bot
                    and (rep.from_user.username or "").lower() == self.username.lower())

    async def on_text(self, message: Message) -> None:
        if not message.from_user or message.from_user.is_bot or not self.addressed(message):
            return
        user = str(message.from_user.id)
        if not self.limit.allow(user):
            log.info("rate_limited", user=user)
            return
        text = message.text or ""
        if self.username:
            text = re.sub(rf"(?i)@{re.escape(self.username)}", "", text).strip()
        private = message.chat.type == ChatType.PRIVATE
        hist = self.history[user]
        try:
            await self.bot.send_chat_action(message.chat.id, "typing")
        except Exception:  # noqa: BLE001
            pass
        reply = await self.pipeline.answer(text, chat=str(message.chat.id), user=user)
        body = to_html(reply.text)
        buying = reply.meta.get("category") == "buy_intent"
        if (reply.outcome == "answered" and is_stbu_topic(text)) or buying:
            try:
                site = await self.site.facts()
                # The buy answer already carries the safety check line.
                body += "\n\n" + links_block(site, compact=not buying, safety=not buying)
            except SiteUnavailable:
                pass
        await self._send(message, body)
        summary = await self.leads.consider(private=private, user_id=user,
                                            name=message.from_user.full_name, text=text,
                                            history=list(hist))
        hist.append(text)
        if summary:
            await self._dm_admins(summary)

    async def _dm_admins(self, text: str) -> None:
        for admin in self.admins:
            try:
                await self.bot.send_message(admin, text, disable_web_page_preview=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("admin_dm_failed", admin=admin, error=type(exc).__name__)


async def run(bot: Bot, stoby: StobyBot) -> None:
    dp = Dispatcher()
    dp.include_router(stoby.router)
    me = await bot.get_me()
    stoby.username = me.username or ""
    # The menu people see is set by the code at every boot, so it can never lag
    # behind the commands the bot really has (28.09.2026: it still listed the
    # previous bot's 16 commands, and /blog fell through to the model).
    from aiogram.types import (
        BotCommand,
        BotCommandScopeAllGroupChats,
        BotCommandScopeAllPrivateChats,
    )

    commands = [BotCommand(command=c, description=d) for c, d in MENU]
    for scope in (None, BotCommandScopeAllPrivateChats(), BotCommandScopeAllGroupChats()):
        try:
            await bot.set_my_commands(commands, scope=scope) if scope else await bot.set_my_commands(commands)
        except Exception as exc:  # noqa: BLE001
            log.warning("menu.set_failed", error=str(exc)[:120])
    log.info("menu.set", commands=[c for c, _ in MENU])
    log.info("telegram.start", username=stoby.username)
    await dp.start_polling(bot, allowed_updates=["message"])


async def heartbeat(path: str, every: int = 60) -> None:
    from pathlib import Path

    while True:
        try:
            Path(path).touch()
        except OSError:
            pass
        await asyncio.sleep(every)
