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
from ...moderation.deleted import is_deleted_account
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
# Addressed-by-name: "Stoby" and common typos, but never "Stobox" (the company).
_NAME_RE = re.compile(r"\bstob(y|i|ie|by|bie|ey)\b", re.I)
# Varied "thinking" placeholders (rotated) so Stoby visibly works, never a bot loop.
_THINKING = [
    "🔍 Checking the Stobox docs…",
    "🔎 One sec — pulling that up…",
    "📚 Digging into the sources…",
    "🧠 Let me check that properly…",
    "⏳ Looking into it…",
]


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
        self.admin_usernames = self.secrets.admin_usernames
        self.proactive: ProactiveScheduler | None = None
        # Group chats the bot has seen — targets for proactive posts.
        self.known_chats: set[str] = set()
        # Follow-up state: token→question (for buttons) and pending email topics.
        self._q_cache: dict[str, str] = {}
        self._email_pending: dict[int, str] = {}
        # Active quiz polls: poll_id → correct_option_id (for XP scoring).
        self._quiz_polls: dict[str, int] = {}
        # AXIS pre-qualifier sessions: user_id → Session.
        self._axis_sessions: dict = {}
        # Running count of deleted (ghost) accounts removed, for /modstats.
        self.deleted_removed: int = 0
        # Rotates the "thinking" placeholder text so it never looks canned.
        self._think_i: int = 0

    async def start(self) -> None:
        from telegram.ext import (
            ApplicationBuilder,
            CallbackQueryHandler,
            ChatMemberHandler,
            CommandHandler,
            InlineQueryHandler,
            MessageHandler,
            PollAnswerHandler,
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
        # Welcome new community members (first 60 seconds decide retention).
        self.app.add_handler(
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self._on_new_members)
        )
        # Inline mode — @bot <query> callable in ANY chat (enable via BotFather
        # /setinline). Answers go through the same compliance pipeline.
        self.app.add_handler(InlineQueryHandler(self._on_inline_query))
        # Mod-log Pardon/Ban buttons.
        self.app.add_handler(CallbackQueryHandler(self._on_mod_callback, pattern=r"^mod:"))
        # Answer follow-ups: More detail / Email me this.
        self.app.add_handler(
            CallbackQueryHandler(self._on_followup_callback, pattern=r"^(more|email):")
        )
        # AMA upvote buttons.
        self.app.add_handler(CallbackQueryHandler(self._on_ama_callback, pattern=r"^ama:"))
        # Interactive /guide navigation.
        self.app.add_handler(CallbackQueryHandler(self._on_guide_callback, pattern=r"^guide:"))
        # AXIS pre-qualifier flow.
        self.app.add_handler(CallbackQueryHandler(self._on_axis_callback, pattern=r"^axis:"))
        # Topic-subscription toggle buttons.
        self.app.add_handler(CallbackQueryHandler(self._on_sub_callback, pattern=r"^sub:"))
        # Tailored resource matcher (from the AXIS result).
        self.app.add_handler(CallbackQueryHandler(self._on_match_callback, pattern=r"^match:"))
        # Quiz scoring: award XP when a member answers a quiz poll correctly.
        self.app.add_handler(PollAnswerHandler(self._on_poll_answer))
        # Group hygiene: auto-remove deleted accounts as their membership surfaces.
        self.app.add_handler(
            ChatMemberHandler(self._on_chat_member, ChatMemberHandler.CHAT_MEMBER)
        )
        self.app.add_error_handler(self._on_error)

        await self.app.initialize()
        me = await self._get_me_with_retry()
        self.bot_username = me.username
        log.info("telegram.start", username=me.username)

        # Proactive engagement (evangelist + inactivity revival).
        self.proactive = ProactiveScheduler(self.engine, self.app)
        self.proactive.schedule()

        await self.app.start()
        # ALL_TYPES so we also receive chat_member updates (used to auto-remove
        # deleted accounts as their membership surfaces).
        from telegram import Update

        await self.app.updater.start_polling(
            drop_pending_updates=True, allowed_updates=Update.ALL_TYPES
        )

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

    def is_admin(self, user) -> bool:
        """Admin by numeric ID (preferred) or by @username (convenience)."""
        if user is None:
            return False
        if user.id in self.admins:
            return True
        uname = (getattr(user, "username", None) or "").lower()
        return bool(uname and uname in self.admin_usernames)

    def _log_message(self, update) -> None:
        """Record a group message to the internal log (best-effort, never blocks)."""
        if not getattr(self.engine, "message_log_enabled", False):
            return
        try:
            msg = update.effective_message
            chat = update.effective_chat
            user = update.effective_user
            reply = msg.reply_to_message
            self.engine.message_log.append(
                chat_id=str(chat.id),
                chat_title=getattr(chat, "title", None) or "",
                user_id=str(user.id) if user else "?",
                username=user.username if user else None,
                display_name=(user.full_name if user else ""),
                text=msg.text or msg.caption or "",
                message_id=str(msg.message_id),
                reply_to=(reply.text if reply and reply.text else None),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.msglog_failed", error=str(exc))

    # ------------------------------------------------------------------ #
    async def _on_message(self, update, context) -> None:
        message = update.effective_message
        if message is None or not (message.text or message.caption):
            return
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            self.known_chats.add(str(chat.id))
            self._log_message(update)
        incoming = self._to_incoming(update)

        # Wallet address / private key pasted → the migration checker (read-only)
        # or an immediate compromise warning. Runs in DMs or when addressed.
        stripped = (message.text or "").strip()
        if incoming.is_private or incoming.raw.get("addressed"):
            from ...chain import is_address, is_private_key

            if is_address(stripped) or is_private_key(stripped):
                try:
                    report = await self.engine.check_wallet(stripped)
                    await self.reply_html(message, report)
                except Exception as exc:  # noqa: BLE001
                    log.error("telegram.wallet_check_failed", error=str(exc))
                return

        # Immediate feedback so nobody stares at silence: show the native
        # "typing…" indicator whenever we'll engage, and for question-like
        # messages also post a rotating "thinking" placeholder we EDIT into the
        # final answer (visible searching/validating while docs are pulled).
        placeholder = None
        will_answer = incoming.is_private or incoming.raw.get("addressed")
        if will_answer:
            try:
                await context.bot.send_chat_action(chat.id, "typing")
            except Exception:  # noqa: BLE001
                pass
        if will_answer and _QUESTION_LIKE.search(incoming.text):
            think = _THINKING[self._think_i % len(_THINKING)]
            self._think_i += 1
            try:
                placeholder = await message.reply_text(think)
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

    async def _on_new_members(self, update, context) -> None:
        """Greet new group members by name. Skips bots; a mass-join (>5 at
        once) is treated as a possible raid — no welcome, admins pinged."""
        message = update.effective_message
        chat = update.effective_chat
        joined = message.new_chat_members or []
        # A joining ghost account gets removed, not welcomed.
        for m in joined:
            if is_deleted_account(m):
                await self._remove_deleted_account(context, chat, m)
        humans = [m for m in joined if not m.is_bot and not is_deleted_account(m)]
        if not humans:
            return
        if chat:
            self.known_chats.add(str(chat.id))
        if len(humans) > 5:
            log.warning("telegram.mass_join", count=len(humans), chat=str(chat.id))
            for admin_id in self.admins:
                try:
                    await context.bot.send_message(
                        admin_id, f"⚠️ Mass join in {chat.title or chat.id}: "
                                  f"{len(humans)} accounts at once — possible raid."
                    )
                except Exception:  # noqa: BLE001
                    pass
            return
        names = ", ".join(m.first_name for m in humans[:5])
        variants = [
            f"👋 Welcome, {names}! I'm Stoby, the resident AI of the Stobox community — "
            f"part monster, part mind, fully awake. Ask me anything about tokenization, "
            f"Compass, or STBU right here, or DM me for a 1:1. Verify me with /sources.",
            f"👋 {names}, good to have you! I'm Stoby. Questions about Stobox, the STBU "
            f"migration, or RWA tokenization? Just ask — I answer from the official docs, "
            f"with sources. (And remember: Stobox staff never DM you first.)",
            f"👋 Welcome aboard, {names}! I'm Stoby, here 24/7 for anything Stobox — ask a "
            f"question, or /help to see what I can do. Verify me with /sources.",
        ]
        try:
            await message.reply_text(variants[len(names) % len(variants)])
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.welcome_failed", error=str(exc))

    def _remove_deleted_enabled(self) -> bool:
        return bool(self.engine.config.get("moderation.remove_deleted_accounts", True))

    async def _remove_deleted_account(self, context, chat, user) -> bool:
        """Kick a deleted (ghost) account from a group. Ban-then-unban so it's a
        removal, not a standing ban. No-op unless enabled and we're in a group.
        Tolerates missing admin rights (logs and moves on)."""
        if not self._remove_deleted_enabled() or chat is None:
            return False
        if getattr(chat, "type", None) not in ("group", "supergroup"):
            return False
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            await context.bot.unban_chat_member(chat.id, user.id, only_if_banned=True)
        except Exception as exc:  # noqa: BLE001 - usually: not admin / lacking ban rights
            log.warning("telegram.deleted_remove_failed", chat=str(chat.id),
                        user=user.id, error=str(exc))
            return False
        self.deleted_removed += 1
        log.info("telegram.deleted_removed", chat=str(chat.id), user=user.id,
                 total=self.deleted_removed)
        return True

    async def _on_chat_member(self, update, context) -> None:
        """A member's status changed — if it surfaces a deleted account that's
        still in the group, remove it (group hygiene)."""
        cm = update.chat_member
        if cm is None:
            return
        new = cm.new_chat_member
        # Only act on members still present (member/restricted), not those who
        # already left/were kicked/banned.
        if new.status not in ("member", "restricted"):
            return
        if is_deleted_account(new.user):
            await self._remove_deleted_account(context, update.effective_chat, new.user)

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
                display_name=user.full_name, is_admin=self.is_admin(user),
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
        title = "Stoby's answer" + ("" if response.confidence.value != "low" else " (limited)")
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

    async def reply_html(self, message, text: str, reply_markup=None) -> None:
        """Send with Telegram HTML parse mode; fall back to stripped plain text
        if the model produced HTML Telegram won't accept (one bad tag would
        otherwise kill the whole message)."""
        from telegram.constants import ParseMode
        from telegram.error import BadRequest

        try:
            await message.reply_text(
                text[:4096], parse_mode=ParseMode.HTML,
                disable_web_page_preview=True, reply_markup=reply_markup,
            )
        except BadRequest as exc:
            log.warning("telegram.html_fallback", error=str(exc))
            await message.reply_text(
                strip_html(text)[:4096], disable_web_page_preview=True,
                reply_markup=reply_markup,
            )

    def _answer_buttons(self, response, question: str, is_private: bool):
        """Progressive-disclosure buttons under a substantive answer: More
        detail, Email me this / Continue in DM, and Share."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        # Only offer follow-ups on real, confident, doc-grounded answers.
        substantive = (
            response.confidence.value != "low"
            and not response.meta.get("gated")
            and not response.meta.get("rails", {}).get("blocked")
            and response.meta.get("mode") != "moderator"
            and bool(question.strip())
        )
        if not substantive or not self.bot_username:
            return None
        token = self._remember_question(question)
        rows = []
        row1 = [InlineKeyboardButton("📖 More detail", callback_data=f"more:{token}")]
        if is_private:
            row1.append(InlineKeyboardButton("📩 Email me this", callback_data=f"email:{token}"))
        else:
            row1.append(InlineKeyboardButton("💬 Continue in DM",
                                             url=f"https://t.me/{self.bot_username}"))
        rows.append(row1)
        if response.meta.get("shareable"):
            rows.append([InlineKeyboardButton("↗️ Share this answer",
                                              switch_inline_query=question[:180])])
        return InlineKeyboardMarkup(rows)

    def _remember_question(self, question: str) -> str:
        """Cache a question behind a short token for callback_data (64-byte cap)."""
        token = str(abs(hash(question)) % 10_000_000)
        self._q_cache[token] = question
        if len(self._q_cache) > 500:            # simple bound
            self._q_cache.pop(next(iter(self._q_cache)))
        return token

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

    async def _alert_fud(self, context, update, response) -> None:
        """DM admins on a coordinated-FUD spike so they can step in fast."""
        chat = update.effective_chat
        count = response.meta.get("fud_alert", 0)
        excerpt = response.meta.get("fud_excerpt", "")
        where = getattr(chat, "title", None) or (chat.id if chat else "?")
        link = ""
        if getattr(chat, "username", None):
            link = f"\nJump in: https://t.me/{chat.username}"
        text = (
            f"🚨 <b>FUD spike</b> in <b>{where}</b>\n"
            f"{count} FUD-flagged messages in a short window. Latest:\n"
            f"“{excerpt}”\n\n"
            f"I'm already replying calmly with facts — a human touch may help.{link}"
        )
        for admin_id in self.admins:
            try:
                await context.bot.send_message(admin_id, text, parse_mode="HTML",
                                               disable_web_page_preview=True)
            except Exception:  # noqa: BLE001
                pass
        log.info("fud.alert_sent", chat=str(getattr(chat, "id", "?")), count=count)

    async def _dm_admins(self, context, text: str) -> None:
        """Best-effort broadcast to every configured admin."""
        for admin_id in self.admins:
            try:
                await context.bot.send_message(admin_id, text, disable_web_page_preview=True)
            except Exception:  # noqa: BLE001 - admin hasn't started the bot, etc.
                pass

    async def notify_mql_admins(self, context, profile) -> bool:
        """DM admins a fresh MQL once (safety net beside the team-inbox email)."""
        if profile.mql_notified or not (profile.email and profile.lead_score >= 40):
            return False
        profile.mql_notified = True
        await self._dm_admins(context, "🟢 New MQL from Telegram\n\n"
                              + self.engine.leads.summary(profile))
        return True

    async def _render(self, update, context, response, placeholder=None) -> None:
        # Coordinated-FUD spike → ping admins immediately (independent of the reply).
        if response.meta.get("fud_alert"):
            await self._alert_fud(context, update, response)
        # Fresh MQL from the passive lead path → DM admins the summary.
        if response.meta.get("mql_summary"):
            await self._dm_admins(context, "🟢 New MQL from Telegram\n\n"
                                  + response.meta["mql_summary"])
        # Benign impersonation flag (name mimics team) → post the mod-log for admins
        # to pardon/act on, but DON'T swallow the reply — keep helping the person.
        if response.meta.get("mod_alert"):
            try:
                await self._post_modlog(
                    context, update.effective_chat, response.meta["mod_alert"],
                    ModerationAction.NONE,
                )
            except Exception:  # noqa: BLE001
                pass
        # Moderation verdict → apply action, DM the offender, post the mod-log.
        if response.moderation != ModerationAction.NONE or response.meta.get("alert_admin"):
            await self._handle_moderation(update, context, response)
            return
        # New unanswered question captured → mirror DRAFT to the register and
        # ping admins with ready-to-use /answer instructions.
        qa_meta = response.meta.get("qa")
        if qa_meta and qa_meta.get("new"):
            await self._notify_new_question(context, qa_meta)
        elif response.escalate:
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
        if response.meta.get("share_nudge") and self.bot_username:
            text += (
                "\n\n🙌 Finding this useful? Share Stobox with a friend — "
                f"https://stobox.io — or just send them my way: @{self.bot_username}"
            )
        markup = self._answer_buttons(
            response, update.effective_message.text or "", update.effective_chat.type == "private"
        )
        if placeholder:
            # Morph the "checking…" message into the answer (no second bubble).
            from telegram.constants import ParseMode
            from telegram.error import BadRequest

            try:
                await placeholder.edit_text(
                    text[:4096], parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True, reply_markup=markup,
                )
            except BadRequest:
                try:
                    await placeholder.edit_text(
                        strip_html(text)[:4096], disable_web_page_preview=True,
                        reply_markup=markup,
                    )
                except Exception:  # noqa: BLE001
                    await self.reply_html(update.effective_message, text, markup)
        else:
            await self.reply_html(update.effective_message, text, markup)

    async def _handle_moderation(self, update, context, response) -> None:
        """Execute the graded action, DM the offender an explanation, and post a
        mod-log to admins with one-tap Pardon / Ban buttons."""
        chat = update.effective_chat
        user = update.effective_user
        msg = update.effective_message
        m = response.meta
        action = response.moderation

        # 1) Execute (delete → mute → ban), tolerant of missing admin rights.
        try:
            if m.get("delete") and action != ModerationAction.NONE:
                await msg.delete()
            if action == ModerationAction.MUTE:
                from telegram import ChatPermissions

                until = msg.date + timedelta(minutes=int(m.get("mute_minutes", 60) or 60))
                await context.bot.restrict_chat_member(
                    chat.id, user.id, ChatPermissions(can_send_messages=False), until_date=until
                )
            elif action == ModerationAction.BAN:
                await context.bot.ban_chat_member(chat.id, user.id)
            if action != ModerationAction.NONE:
                log.info("telegram.moderation_applied", action=action.value, user=user.id,
                         category=m.get("category"), strike=m.get("strike_count"))
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.moderation_failed", action=action.value, error=str(exc))

        # 2) Public note (WARN only — keep the chat clean, don't spotlight).
        if response.text:
            try:
                await msg.reply_text(response.text)
            except Exception:  # noqa: BLE001
                pass

        # 3) DM the offender an explanation + appeal path.
        dm = m.get("dm_text")
        if dm and action != ModerationAction.NONE:
            try:
                await context.bot.send_message(user.id, dm)
            except Exception:  # noqa: BLE001 - user hasn't started the bot; that's fine
                pass

        # 4) Mod-log to admins with actionable buttons.
        await self._post_modlog(context, chat, m, action)

    async def _post_modlog(self, context, chat, m: dict, action) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        uk = m.get("offender_user_key", "")
        name = m.get("offender_name") or m.get("offender_id")
        cat = m.get("category", "?")
        excerpt = (m.get("reason") or "")[:200]
        title = "🛡 Impersonation flag" if action == ModerationAction.NONE else "🛡 Moderation action"
        text = (
            f"{title} in <b>{getattr(chat, 'title', None) or chat.id}</b>\n"
            f"User: {name}\nCategory: <b>{cat}</b> · Action: <b>{action.value}</b> · "
            f"Strike {m.get('strike_count', '?')}\n"
        )
        if excerpt:
            text += f"Why: {excerpt}\n"
        buttons = [InlineKeyboardButton("↩️ Pardon", callback_data=f"mod:pardon:{uk}")]
        if action != ModerationAction.BAN:
            buttons.append(InlineKeyboardButton("⛔ Ban", callback_data=f"mod:ban:{uk}:{chat.id}"))
        markup = InlineKeyboardMarkup([buttons])
        for admin_id in self.admins:
            try:
                await context.bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=markup)
            except Exception:  # noqa: BLE001
                pass

    async def _on_followup_callback(self, update, context) -> None:
        """User taps 'More detail' or 'Email me this' under an answer."""
        query = update.callback_query
        verb, _, token = (query.data or "").partition(":")
        question = self._q_cache.get(token)
        if not question:
            await query.answer("That one expired — just ask me again. 🙂", show_alert=True)
            return

        if verb == "more":
            await query.answer("Pulling the full version…")
            try:
                resp = await self.engine.detailed_answer(
                    question, user_key=f"telegram:{query.from_user.id}"
                )
                body = (resp.text + self.render_citations(resp))[:4096]
                await context.bot.send_message(
                    query.from_user.id if query.message.chat.type != "private"
                    else query.message.chat.id,
                    body, parse_mode="HTML", disable_web_page_preview=True,
                )
            except Exception as exc:  # noqa: BLE001
                log.error("telegram.more_failed", error=str(exc))
                await context.bot.send_message(query.message.chat.id,
                                               "Sorry — couldn't expand that. Try asking directly.")
        elif verb == "email":
            self._email_pending[query.from_user.id] = question
            await query.answer()
            await context.bot.send_message(
                query.from_user.id,
                "📩 Happy to email you the full breakdown. Just reply with:\n"
                "<code>/email you@example.com</code>\n\n"
                "I'll never share your address, and you can ignore this if you'd rather not.",
                parse_mode="HTML",
            )

    def _axis_question_markup(self, step: int):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        from ...leads.axis import QUESTIONS
        q = QUESTIONS[step]
        rows, row = [], []
        for i, (label, _v, _p) in enumerate(q.options):
            row.append(InlineKeyboardButton(label, callback_data=f"axis:{step}:{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return q.prompt, InlineKeyboardMarkup(rows)

    async def start_axis(self, message, user) -> None:
        from ...leads.axis import Session
        self._axis_sessions[user.id] = Session()
        prompt, markup = self._axis_question_markup(0)
        await message.reply_text(
            "Let's do a quick <b>fit check</b> — 5 taps, ~30 seconds. It points you to the "
            "right next step (it's a light indicator, not the full Readiness Score).\n\n" + prompt,
            parse_mode="HTML", reply_markup=markup,
        )

    async def _on_axis_callback(self, update, context) -> None:
        from ...leads import axis as ax

        query = update.callback_query
        parts = (query.data or "").split(":")
        if len(parts) != 3:
            await query.answer()
            return
        session = self._axis_sessions.get(query.from_user.id)
        step, idx = int(parts[1]), int(parts[2])
        if not session or session.step != step or idx >= len(ax.QUESTIONS[step].options):
            await query.answer("This check expired — send /qualify to start again.")
            return
        session.record(ax.QUESTIONS[step], idx)
        await query.answer()
        if not session.done:
            prompt, markup = self._axis_question_markup(session.step)
            try:
                await query.edit_message_text(prompt, parse_mode="HTML", reply_markup=markup)
            except Exception:  # noqa: BLE001
                pass
            return
        # Done → result + warm-lead capture.
        self._axis_sessions.pop(query.from_user.id, None)
        text = ax.result_text(session, query.from_user.first_name or "")
        asset = session.answers.get("asset", "")
        juris = session.answers.get("jurisdiction", "")
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📚 Resources for my case", callback_data=f"match:{asset}:{juris}")]])
        try:
            await query.edit_message_text(text, parse_mode="HTML",
                                          reply_markup=markup, disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            await context.bot.send_message(query.message.chat.id, text,
                                           parse_mode="HTML", reply_markup=markup)
        await self._capture_axis_lead(context, query.from_user, session)

    async def _on_match_callback(self, update, context) -> None:
        """Show the tailored resource pack from the AXIS 'Resources' button."""
        from ...leads import matcher

        query = update.callback_query
        parts = (query.data or "match::").split(":")
        asset = parts[1] if len(parts) > 1 else ""
        juris = parts[2] if len(parts) > 2 else ""
        text = matcher.match(asset, juris, query.from_user.first_name or "")
        try:
            await context.bot.send_message(query.message.chat.id, text,
                                           parse_mode="HTML", disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            pass
        await query.answer()

    async def _capture_axis_lead(self, context, user, session) -> None:
        try:
            uk = f"telegram:{user.id}"
            profile = await self.engine.memory.get_profile(uk, user.full_name)
            profile.customer_stage = "evaluating"
            for k, v in session.answers.items():
                profile.notes = (profile.notes + f" {k}={v};").strip()
                if k == "asset":
                    profile.add_product(v)
            self.engine.leads.update_score(profile, buying_intent=True, has_email=bool(profile.email))
            await self.engine.leads.handoff(profile)
            await self.notify_mql_admins(context, profile)   # no-op unless email + qualified
            await self.engine.memory.save_profile(profile)
            self.engine.xp.award(uk, 5, "qualified", user.full_name)
            from ...leads.axis import band
            log.info("axis.qualified", user=uk, score=session.score, band=band(session.score),
                     answers=session.answers)
        except Exception as exc:  # noqa: BLE001
            log.warning("axis.capture_failed", error=str(exc))

    async def _on_guide_callback(self, update, context) -> None:
        """Navigate the interactive /guide (menu ⇄ sections)."""
        from . import commands as cmd

        query = update.callback_query
        section = (query.data or "guide:menu").split(":", 1)[1]
        text, markup = cmd.guide_view(section)
        try:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
            )
        except Exception:  # noqa: BLE001 - identical content / too old to edit
            pass
        await query.answer()

    async def _on_sub_callback(self, update, context) -> None:
        """A member toggles a topic subscription from the /subscribe menu."""
        from ...ops.subscriptions import TOPICS, valid_topic
        from . import commands as cmd

        query = update.callback_query
        topic = (query.data or "sub:").split(":", 1)[1]
        book = self.engine.subscriptions
        chat_id = str(query.from_user.id)
        if topic == "__all_off":
            book.unsubscribe_all(chat_id)
            note = "All topics off. 🔕"
        elif valid_topic(topic):
            now_on = book.toggle(chat_id, topic)
            note = f"{'On' if now_on else 'Off'}: {TOPICS[topic]['label']}"
        else:
            await query.answer()
            return
        try:
            await query.edit_message_text(
                cmd._subs_summary(chat_id, book), parse_mode="HTML",
                reply_markup=cmd._subs_markup(chat_id, book),
                disable_web_page_preview=True,
            )
        except Exception:  # noqa: BLE001 - identical content / too old to edit
            pass
        await query.answer(note)

    async def _on_ama_callback(self, update, context) -> None:
        """A member taps 👍 to upvote an AMA question."""
        query = update.callback_query
        parts = (query.data or "").split(":")
        if len(parts) != 3 or parts[1] != "up" or not parts[2].isdigit():
            await query.answer()
            return
        qid = int(parts[2])
        votes = self.engine.ama.upvote(qid, f"telegram:{query.from_user.id}")
        if votes < 0:
            await query.answer("That question's no longer in the queue.")
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup([[
                InlineKeyboardButton(f"👍 Upvote ({votes})", callback_data=f"ama:up:{qid}")
            ]]))
        except Exception:  # noqa: BLE001 - message may be too old to edit
            pass
        await query.answer("Vote counted! 👍")

    async def send_quiz(self, context, chat_id) -> bool:
        """Generate + post a native Telegram quiz poll; track it for XP scoring."""
        quiz = await self.engine.generate_quiz()
        if not quiz:
            return False
        try:
            msg = await context.bot.send_poll(
                chat_id, quiz["question"], quiz["options"],
                type="quiz", correct_option_id=quiz["correct_index"],
                explanation=quiz["explanation"] or None, is_anonymous=False,
            )
            self._quiz_polls[msg.poll.id] = quiz["correct_index"]
            if len(self._quiz_polls) > 200:
                self._quiz_polls.pop(next(iter(self._quiz_polls)))
            log.info("quiz.posted", chat=chat_id)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("quiz.post_failed", error=str(exc))
            return False

    async def _on_poll_answer(self, update, context) -> None:
        ans = update.poll_answer
        correct = self._quiz_polls.get(ans.poll_id)
        if correct is None or not ans.option_ids:
            return
        user = ans.user
        uk = f"telegram:{user.id}"
        self.engine.xp.touch(uk, user.full_name)
        if ans.option_ids[0] == correct:
            self.engine.xp.award(uk, 10, "quiz_correct", user.full_name)
            try:
                await context.bot.send_message(
                    user.id, "🎉 Correct! +10 XP. Nice one. Check your standing with /rank."
                )
            except Exception:  # noqa: BLE001 - user hasn't opened a DM; that's fine
                pass

    async def _on_mod_callback(self, update, context) -> None:
        """Admin taps Pardon / Ban in the mod-log."""
        query = update.callback_query
        if not self.is_admin(query.from_user):
            await query.answer("Admins only.", show_alert=True)
            return
        parts = (query.data or "").split(":")
        _, verb, uk = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
        if verb == "pardon" and uk:
            self.engine.strikes.pardon(uk)
            # Un-ban / un-mute in every group the bot knows, best-effort.
            uid = uk.split(":")[-1]
            for chat_id in self.known_chats:
                try:
                    await context.bot.unban_chat_member(chat_id, int(uid), only_if_banned=True)
                except Exception:  # noqa: BLE001
                    pass
            await query.answer("Pardoned — strike removed.")
            await query.edit_message_text((query.message.text or "") + "\n\n✅ Pardoned.")
        elif verb == "ban" and uk:
            uid = uk.split(":")[-1]
            chat_id = parts[3] if len(parts) > 3 else None
            self.engine.strikes.set_banned(uk, True)
            try:
                if chat_id:
                    await context.bot.ban_chat_member(int(chat_id), int(uid))
            except Exception:  # noqa: BLE001
                pass
            await query.answer("Banned.")
            await query.edit_message_text((query.message.text or "") + "\n\n⛔ Banned by admin.")
        else:
            await query.answer()

    async def _notify_new_question(self, context, qa_meta: dict) -> None:
        """Mirror the DRAFT into the stobox-v15 register and DM the admins."""
        import asyncio as _asyncio

        from ...qa import mirror

        qid = qa_meta["qid"]
        entry = self.engine.qa.get(qid)
        if entry and entry.register_number is None:
            number = await _asyncio.to_thread(mirror.push_draft, entry)
            if number:
                entry.register_number = number
                self.engine.qa._save()
        # Propose a draft so the admin reviews instead of writing from scratch.
        draft = ""
        if entry and not entry.draft:
            try:
                draft = await self.engine.draft_answer(entry.question)
            except Exception:  # noqa: BLE001
                draft = ""
            if draft:
                entry.draft = draft
                self.engine.qa._save()
        elif entry:
            draft = entry.draft
        text = (
            f"❓ <b>New unanswered question #{qid}</b>\n\n"
            f"“{qa_meta['question']}”\n\n"
        )
        if draft:
            text += (
                f"🤖 <b>Proposed draft</b> (not sent to anyone):\n{draft[:1200]}\n\n"
                f"✅ <code>/approve {qid}</code> — use this draft as-is\n"
                f"✏️ <code>/answer {qid} your better answer</code> — replace it\n"
            )
        else:
            text += f"Reply with:\n<code>/answer {qid} your answer here</code>\n"
        text += (
            "I'll save it to the Community QA register, start using it, and "
            "follow up with everyone who asked. /pending lists open questions."
        )
        for admin_id in self.admins:
            try:
                await context.bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception:  # noqa: BLE001
                pass

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
            is_admin=self.is_admin(user),
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
        # By @username…
        if self.bot_username and f"@{self.bot_username}".lower() in text.lower():
            return True
        # …by name ("Hey Stoby", and common typos) — always react…
        if _NAME_RE.search(text or ""):
            return True
        # …or a reply to one of Stoby's messages.
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
