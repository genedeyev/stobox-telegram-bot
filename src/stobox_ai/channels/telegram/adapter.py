"""Telegram adapter (python-telegram-bot v21, async).

Responsibilities:
  * translate Telegram updates → :class:`IncomingMessage` (groups, supergroups,
    channels, private, forum topics, replies, edits, attachments, links);
  * run them through the shared engine and render the reply + citations;
  * execute moderation actions (delete / mute / ban) the engine requests;
  * register commands and proactive jobs (evangelist + inactivity revival).
"""

from __future__ import annotations

import re
from datetime import timedelta

from ...config import Secrets, get_secrets
from ...core.engine import AgentEngine
from ...core.types import (
    Attachment,
    Author,
    ChatType,
    IncomingMessage,
    ModerationAction,
)
from ...logging import get_logger
from ..base import Channel
from . import commands as cmd
from .proactive import ProactiveScheduler

log = get_logger(__name__)
_URL = re.compile(r"https?://\S+")
_HTML_TAGS = re.compile(r"</?(b|strong|i|em|u|s|code|pre|a|tg-spoiler)(\s[^>]*)?>", re.I)
# Cheap "this will need retrieval" heuristic → show the searching placeholder.
_QUESTION_LIKE = re.compile(
    r"\?|^\s*(what|how|why|when|where|which|who|can|does|do|is|are|explain|tell)\b", re.I
)


def strip_html(text: str) -> str:
    """Plain-text fallback when Telegram rejects the HTML parse."""
    return _HTML_TAGS.sub("", text)

_CHAT_TYPES = {
    "private": ChatType.PRIVATE,
    "group": ChatType.GROUP,
    "supergroup": ChatType.SUPERGROUP,
    "channel": ChatType.CHANNEL,
}


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, engine: AgentEngine, secrets: Secrets | None = None) -> None:
        super().__init__(engine)
        self.secrets = secrets or get_secrets()
        self.app = None
        self.bot_username: str | None = None
        self.admins = self.secrets.admin_user_ids
        self.proactive: ProactiveScheduler | None = None
        # Group chats the bot has seen — targets for proactive posts.
        self.known_chats: set[str] = set()

    async def start(self) -> None:
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            InlineQueryHandler,
            MessageHandler,
            filters,
        )

        if not self.secrets.telegram_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

        # Generous timeouts: PTB's 5s defaults die on slow Wi-Fi / flaky IPv6
        # paths to api.telegram.org. Polling read timeout is higher by design.
        self.app = (
            ApplicationBuilder()
            .token(self.secrets.telegram_token)
            .connect_timeout(20)
            .read_timeout(20)
            .write_timeout(20)
            .pool_timeout(20)
            .get_updates_read_timeout(40)
            .build()
        )
        self.app.bot_data["engine"] = self.engine
        self.app.bot_data["adapter"] = self

        # User + admin commands (see commands.py).
        for name, handler in cmd.registry().items():
            self.app.add_handler(CommandHandler(name, handler))

        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        # Inline mode — @bot <query> callable in ANY chat (enable via BotFather
        # /setinline). Answers go through the same compliance pipeline.
        self.app.add_handler(InlineQueryHandler(self._on_inline_query))
        self.app.add_error_handler(self._on_error)

        await self.app.initialize()
        me = await self._get_me_with_retry()
        self.bot_username = me.username
        log.info("telegram.start", username=me.username)

        # Proactive engagement (evangelist + inactivity revival).
        self.proactive = ProactiveScheduler(self.engine, self.app)
        self.proactive.schedule()

        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)

    async def _get_me_with_retry(self, attempts: int = 4):
        """Retry startup handshake — transient TimedOut must not kill the boot."""
        import asyncio

        from telegram.error import NetworkError, TimedOut

        last_exc: Exception | None = None
        for i in range(1, attempts + 1):
            try:
                return await self.app.bot.get_me()
            except (TimedOut, NetworkError) as exc:
                last_exc = exc
                wait = 3 * i
                log.warning("telegram.get_me_retry", attempt=i, wait_s=wait, error=str(exc))
                await asyncio.sleep(wait)
        raise last_exc  # exhausted — a real network problem, surface it

    async def stop(self) -> None:
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            log.info("telegram.stopped")

    # ------------------------------------------------------------------ #
    async def _on_message(self, update, context) -> None:
        message = update.effective_message
        if message is None or not (message.text or message.caption):
            return
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            self.known_chats.add(str(chat.id))
        incoming = self._to_incoming(update)

        # Immediate feedback: for question-like messages we'll answer, post a
        # "checking the docs" placeholder right away, then EDIT it into the
        # final answer — the user is never left staring at silence.
        placeholder = None
        will_answer = incoming.is_private or incoming.raw.get("addressed")
        if will_answer and _QUESTION_LIKE.search(incoming.text):
            try:
                placeholder = await message.reply_text("🔍 Checking the Stobox docs…")
            except Exception:  # noqa: BLE001
                placeholder = None

        try:
            response = await self.engine.handle(incoming)
        except Exception as exc:  # noqa: BLE001
            log.error("telegram.handle_failed", error=str(exc))
            # Never fail silently in a DM — tell the user and move on.
            apology = (
                "Sorry — something went wrong on my side processing that. "
                "Please try again in a moment, or email support@stobox.io."
            )
            try:
                if placeholder:
                    await placeholder.edit_text(apology)
                elif incoming.is_private:
                    await message.reply_text(apology)
            except Exception:  # noqa: BLE001
                pass
            return

        if response is None:
            if placeholder:  # engine chose not to reply — clean up quietly
                try:
                    await placeholder.delete()
                except Exception:  # noqa: BLE001
                    pass
            return
        await self._render(update, context, response, placeholder=placeholder)

    async def _on_inline_query(self, update, context) -> None:
        """Answer @bot <query> from any chat, via the full compliance pipeline."""
        inline = update.inline_query
        query = (inline.query or "").strip()
        if len(query) < 3:
            await inline.answer([], cache_time=5)
            return
        user = inline.from_user
        incoming = IncomingMessage(
            author=Author(
                external_id=str(user.id), channel="telegram", username=user.username,
                display_name=user.full_name, is_admin=user.id in self.admins,
            ),
            text=query, chat_id=f"inline:{user.id}", chat_type=ChatType.PRIVATE,
            message_id=str(inline.id), channel="telegram",
            raw={"addressed": True, "inline": True},
        )
        try:
            response = await self.engine.handle(incoming)
        except Exception as exc:  # noqa: BLE001
            log.error("telegram.inline_failed", error=str(exc))
            await inline.answer([], cache_time=5)
            return
        if response is None or not response.should_reply:
            await inline.answer([], cache_time=5)
            return

        from telegram import InlineQueryResultArticle, InputTextMessageContent
        from telegram.constants import ParseMode

        body = (response.text + self.render_citations(response))[:4000]
        title = "Stobox answer" + ("" if response.confidence.value != "low" else " (limited)")
        result = InlineQueryResultArticle(
            id=str(inline.id),
            title=title,
            description=strip_html(response.text)[:120],
            input_message_content=InputTextMessageContent(
                body, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            ),
        )
        # is_personal so per-user rate limits / rails aren't cached across users.
        await inline.answer([result], cache_time=30, is_personal=True)

    async def reply_html(self, message, text: str) -> None:
        """Send with Telegram HTML parse mode; fall back to stripped plain text
        if the model produced HTML Telegram won't accept (one bad tag would
        otherwise kill the whole message)."""
        from telegram.constants import ParseMode
        from telegram.error import BadRequest

        try:
            await message.reply_text(
                text[:4096], parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except BadRequest as exc:
            log.warning("telegram.html_fallback", error=str(exc))
            await message.reply_text(strip_html(text)[:4096], disable_web_page_preview=True)

    async def process_query(self, update, context, query: str) -> None:
        """Run arbitrary text (e.g. from a slash command) through the full
        engine pipeline and render the reply. Marked as addressed so the engine
        always answers."""
        incoming = self._to_incoming(update)
        incoming.text = query
        incoming.raw["addressed"] = True
        response = await self.engine.handle(incoming)
        if response and response.should_reply:
            footer = self.render_citations(response)
            await self.reply_html(update.effective_message, response.text + footer)

    async def _render(self, update, context, response, placeholder=None) -> None:
        # Moderation actions first.
        if response.moderation != ModerationAction.NONE:
            await self._apply_moderation(update, context, response)
        if response.escalate:
            await self._escalate(update, context, response)
        if not response.should_reply:
            if placeholder:
                try:
                    await placeholder.delete()
                except Exception:  # noqa: BLE001
                    pass
            return
        footer = self.render_citations(response)
        text = response.text + footer
        if placeholder:
            # Morph the "checking…" message into the answer (no second bubble).
            from telegram.constants import ParseMode
            from telegram.error import BadRequest

            try:
                await placeholder.edit_text(
                    text[:4096], parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except BadRequest:
                try:
                    await placeholder.edit_text(
                        strip_html(text)[:4096], disable_web_page_preview=True
                    )
                except Exception:  # noqa: BLE001
                    await self.reply_html(update.effective_message, text)
        else:
            await self.reply_html(update.effective_message, text)

    async def _apply_moderation(self, update, context, response) -> None:
        chat = update.effective_chat
        user = update.effective_user
        msg = update.effective_message
        action = response.moderation
        try:
            if action == ModerationAction.DELETE:
                await msg.delete()
            elif action == ModerationAction.MUTE:
                from telegram import ChatPermissions

                until = msg.date + timedelta(
                    minutes=int(self.engine.config.get("moderation.mute_minutes", 60))
                )
                await context.bot.restrict_chat_member(
                    chat.id, user.id, ChatPermissions(can_send_messages=False), until_date=until
                )
            elif action == ModerationAction.BAN:
                await msg.delete()
                await context.bot.ban_chat_member(chat.id, user.id)
            log.info("telegram.moderation_applied", action=action.value, user=user.id)
        except Exception as exc:  # noqa: BLE001 - lacking admin rights, etc.
            log.warning("telegram.moderation_failed", action=action.value, error=str(exc))

    async def _escalate(self, update, context, response) -> None:
        """Notify admins of scams/low-confidence escalations."""
        text = (
            f"🚨 Escalation in {update.effective_chat.title or 'chat'} "
            f"({response.meta.get('category', 'low_confidence')}). "
            f"Message: {update.effective_message.text[:200]}"
        )
        for admin_id in self.admins:
            try:
                await context.bot.send_message(admin_id, text)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    def _to_incoming(self, update) -> IncomingMessage:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        text = message.text or message.caption or ""

        author = Author(
            external_id=str(user.id) if user else "unknown",
            channel="telegram",
            username=user.username if user else None,
            display_name=(user.full_name if user else None),
            is_admin=bool(user and user.id in self.admins),
        )
        addressed = self._is_addressed(message, text)
        return IncomingMessage(
            author=author,
            text=text,
            chat_id=str(chat.id),
            chat_type=_CHAT_TYPES.get(chat.type, ChatType.GROUP),
            message_id=str(message.message_id),
            channel="telegram",
            thread_id=str(message.message_thread_id) if message.message_thread_id else None,
            reply_to_text=(
                message.reply_to_message.text
                if message.reply_to_message and message.reply_to_message.text
                else None
            ),
            is_forwarded=bool(getattr(message, "forward_origin", None)),
            is_edited=bool(update.edited_message),
            attachments=self._attachments(message),
            links=_URL.findall(text),
            raw={"addressed": addressed},
        )

    def _is_addressed(self, message, text: str) -> bool:
        if self.bot_username and f"@{self.bot_username}".lower() in text.lower():
            return True
        reply = message.reply_to_message
        if reply and reply.from_user and self.bot_username:
            return reply.from_user.username == self.bot_username
        return False

    @staticmethod
    def _attachments(message) -> list[Attachment]:
        out: list[Attachment] = []
        if message.photo:
            out.append(Attachment.IMAGE)
        if message.document:
            name = (message.document.file_name or "").lower()
            out.append(Attachment.PDF if name.endswith(".pdf") else Attachment.DOCUMENT)
        if message.voice:
            out.append(Attachment.VOICE)
        if message.video:
            out.append(Attachment.VIDEO)
        if message.sticker:
            out.append(Attachment.STICKER)
        return out

    async def _on_error(self, update, context) -> None:
        log.error("telegram.error", error=str(context.error))
