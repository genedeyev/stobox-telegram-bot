"""Proactive engagement for Telegram.

Three jobs, all config-driven:
  * Evangelist   — every N hours, post grounded educational content to active
    group chats (rotating format, avoiding repetition), respecting quiet hours.
  * Revival      — nudge a chat that's gone quiet for too long.
  * Daily digest — send admins a community report (top questions, leads, spam…).

Requires the PTB job-queue extra; if unavailable, proactive posting is skipped
gracefully (the bot still answers reactively).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from ...core.types import Mode
from ...llm.base import ChatMessage
from ...logging import get_logger

log = get_logger(__name__)

_FORMATS = [
    "Tip of the day", "Feature spotlight", "RWA education", "Security advice",
    "Tokenization myth-buster", "Case study angle", "Product update recap",
    "Poll (pose one question)",
]

# Rotating friendly openers for new-blog announcements (deterministic — no LLM
# in broadcasts). Index rotates per announcement so consecutive posts differ.
_BLOG_OPENERS = [
    "📰 Fresh from the Stobox blog — check out our new article:",
    "🆕 Just published on the Stobox blog:",
    "📚 New read for you — hot off the Stobox blog:",
    "✍️ The Stobox team just published something new:",
]


async def fetch_og_meta(url: str, timeout: float = 15.0) -> dict:
    """Fetch a page's OpenGraph meta (image, title, description). Best-effort —
    returns {} on any failure so announcements degrade to a link card."""
    try:
        import httpx
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {}
            soup = BeautifulSoup(resp.text, "html.parser")
            out = {}
            for key in ("image", "title", "description"):
                tag = soup.find("meta", property=f"og:{key}")
                if tag and tag.get("content"):
                    out[key] = tag["content"].strip()
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("blog.og_fetch_failed", url=url, error=str(exc))
        return {}


class ProactiveScheduler:
    def __init__(self, engine, app) -> None:
        self.engine = engine
        self.app = app
        self._recent_posts: list[str] = []
        self._opener_i = 0

    def schedule(self) -> None:
        jq = getattr(self.app, "job_queue", None)
        if jq is None:
            log.warning("proactive.no_job_queue", hint="install python-telegram-bot[job-queue]")
            return
        cfg = self.engine.config
        if cfg.get("proactive.evangelist.enabled", True):
            hours = float(cfg.get("proactive.evangelist.interval_hours", 4))
            jq.run_repeating(self._evangelist_job, interval=hours * 3600, first=hours * 3600)
        if cfg.get("proactive.growth.revive_inactive", True):
            jq.run_repeating(self._revival_job, interval=1800, first=1800)  # check every 30m
        if cfg.get("observability.daily_digest", True):
            jq.run_repeating(self._digest_job, interval=24 * 3600, first=24 * 3600)
        # Daily knowledge reconciliation (ARCHITECTURE.md §2.2) at 04:00 UTC.
        if cfg.get("knowledge.daily_resync", True):
            from datetime import time as _time

            jq.run_daily(self._resync_job, time=_time(4, 0, tzinfo=UTC))
        # New-blog announcements: cheap in-memory diff, so a tight interval is
        # fine — a post lands minutes after the sync that discovers it.
        blog = cfg.section("proactive.blog_announcements")
        if blog.get("enabled", True):
            minutes = float(blog.get("interval_minutes", 10))
            jq.run_repeating(self._blog_announce_job, interval=minutes * 60, first=120)
        # Opt-in migration reminders: hourly check against canonical dates;
        # per-threshold dedupe means each blast fires exactly once.
        if cfg.get("proactive.reminders.enabled", True):
            jq.run_repeating(self._reminder_job, interval=3600, first=300)
        # Opt-in win-back: check quiet subscribers a few times a day.
        if cfg.get("proactive.winback.enabled", True):
            jq.run_repeating(self._winback_job, interval=6 * 3600, first=1800)
        # Weekly content-ideas preview to admins (from community question-gaps).
        if cfg.get("proactive.content.enabled", True):
            jq.run_repeating(self._content_job, interval=7 * 24 * 3600, first=6 * 3600)
        log.info("proactive.scheduled")

    async def _content_job(self, context) -> None:
        """DM admins a weekly preview of blog outlines drafted from question-gaps."""
        admins = getattr(self.app.bot_data.get("adapter"), "admins", set())
        if not admins:
            return
        try:
            results = await self.engine.flywheel.run(
                self.engine.decisions.records(), dry_run=True, limit=5
            )
        except Exception as exc:  # noqa: BLE001
            log.error("content.job_failed", error=str(exc))
            return
        if not results:
            return
        lines = [f"📝 Weekly content ideas ({len(results)}) — from community questions:"]
        for r in results:
            tag = "🕳 gap" if r["is_gap"] else f"{r['count']}×"
            lines.append(f"• {tag} — {r['title'][:80]}")
        lines.append("\nRun /content file to open these as GitHub issues.")
        text = "\n".join(lines)
        for admin_id in admins:
            try:
                await context.bot.send_message(admin_id, text, disable_web_page_preview=True)
            except Exception:  # noqa: BLE001
                pass
        log.info("content.previewed", count=len(results))

    async def _reminder_job(self, context) -> None:
        from ...guardrails.canonicals import _as_date
        from ...guardrails.rails import IMPERSONATION_WARNING
        from ...ops.reminders import THRESHOLDS

        book = self.engine.reminders
        if not book.subscribers or self._in_quiet_hours():
            return
        canon = self.engine.assembler.canonicals if self.engine.assembler else None
        if canon is None:
            return
        m = canon.get("tokens.stbu.migration", {}) or {}
        deadline = _as_date(m.get("burn_deadline"))
        claims = _as_date(m.get("claim_opens"))
        today = datetime.now(UTC).date()

        blast: tuple[str, str] | None = None   # (tag, message)
        if deadline and today <= deadline:
            days_left = (deadline - today).days
            if days_left in THRESHOLDS and not book.was_sent(f"burn-{days_left}"):
                when = "TODAY" if days_left == 0 else (
                    "tomorrow" if days_left == 1 else f"in {days_left} days")
                blast = (
                    f"burn-{days_left}",
                    f"⏰ <b>STBU migration reminder</b> — the burn deadline is "
                    f"<b>{when}</b> ({deadline.strftime('%d %B %Y')} 23:59 UTC).\n\n"
                    "Burn-and-mint, 1:1, same wallet only. Consolidate all STBU into "
                    "one wallet first. Legacy V1 tokens are not eligible. Full steps: "
                    "/migrate\n\n" + IMPERSONATION_WARNING +
                    "\n\nStop these reminders anytime: /stopreminders",
                )
        elif claims and today >= claims and not book.was_sent("claims-open"):
            blast = (
                "claims-open",
                "🟢 <b>STBU claims are open</b> — if you burned before the deadline, "
                "you can now claim on Base (same wallet). Details: /migrate or "
                "https://stobox.io\n\n" + IMPERSONATION_WARNING +
                "\n\nStop these reminders: /stopreminders",
            )
        if not blast:
            return
        tag, text = blast
        sent = 0
        for chat_id in list(book.subscribers):
            try:
                await context.bot.send_message(chat_id, text, parse_mode="HTML",
                                               disable_web_page_preview=True)
                sent += 1
            except Exception:  # noqa: BLE001 - blocked bot etc.
                pass
        book.mark_sent(tag)
        log.info("reminders.blast", tag=tag, sent=sent, subscribers=len(book.subscribers))

    async def _blog_announce_job(self, context) -> None:
        """Announce newly published blog posts with an OG-image card."""
        if self._in_quiet_hours():
            return  # posts stay queued (un-marked) and go out after quiet hours
        new_posts = self.engine.pop_new_blog_posts()
        if not new_posts:
            return
        cfg = self.engine.config.section("proactive.blog_announcements")
        chats = {str(c) for c in (cfg.get("chat_ids") or [])} | self._known_chats()
        if not chats:
            log.info("blog.no_chats_to_announce", posts=len(new_posts))
            return

        for post in new_posts[:3]:  # cap per tick — never flood the chat
            og = await fetch_og_meta(post["url"])
            title = og.get("title") or post["title"]
            teaser = (og.get("description") or "").strip()
            opener = _BLOG_OPENERS[self._opener_i % len(_BLOG_OPENERS)]
            self._opener_i += 1
            caption = f"{opener}\n\n<b>{title}</b>"
            if teaser:
                caption += f"\n{teaser[:220]}"
            caption += (
                f"\n\n🔗 {post['url']}"
                "\n\nGive it a read — questions welcome right here. 👇"
                "\n🔁 Know someone who'd find this useful? Forward it their way."
            )
            delivered = False
            for chat_id in chats:
                try:
                    if og.get("image"):
                        await context.bot.send_photo(
                            chat_id, photo=og["image"], caption=caption[:1024],
                            parse_mode="HTML",
                        )
                    else:
                        # No OG image → let Telegram render the link-preview card.
                        await context.bot.send_message(
                            chat_id, caption[:4096], parse_mode="HTML",
                        )
                    delivered = True
                except Exception as exc:  # noqa: BLE001
                    log.warning("blog.announce_failed", chat=chat_id, error=str(exc))
            if delivered:
                self.engine.mark_blog_announced(post["url"])
                log.info("blog.announced", url=post["url"], chats=len(chats))
            await self._push_to_subscribers(context, title, teaser, post["url"])

    async def _push_to_subscribers(self, context, title, teaser, url) -> None:
        """DM a new post to opted-in topic subscribers (never-initiate: opt-in only)."""
        book = getattr(self.engine, "subscriptions", None)
        if not book:
            return
        from ...ops.subscriptions import TOPICS, classify_topic

        topic = classify_topic(title, teaser)
        if not topic:
            return  # unroutable → group announcement only, no DMs
        recipients = book.subscribers_for(topic)
        if not recipients:
            return
        label = TOPICS[topic]["label"]
        body = f"<b>{title}</b>"
        if teaser:
            body += f"\n{teaser[:220]}"
        dm = (f"{label} — new from Stobox 👇\n\n{body}\n\n🔗 {url}"
              f"\n\n<i>You're subscribed to {label}. Manage or stop with /subscribe.</i>")
        sent = 0
        for chat_id, _lang in recipients:
            try:
                await context.bot.send_message(
                    chat_id, dm[:4096], parse_mode="HTML", disable_web_page_preview=False
                )
                sent += 1
            except Exception as exc:  # noqa: BLE001 - user may have blocked the bot
                log.warning("subs.push_failed", chat=chat_id, error=str(exc))
        log.info("subs.pushed", topic=topic, url=url, sent=sent)

    async def _winback_job(self, context) -> None:
        """One gentle check-in for quiet topic subscribers (opt-in only, cooldowned)."""
        if self._in_quiet_hours():
            return
        subs = getattr(self.engine, "subscriptions", None)
        book = getattr(self.engine, "winback", None)
        if not subs or not book:
            return
        cfg = self.engine.config
        inactive_days = int(cfg.get("proactive.winback.inactive_days", 14))
        cooldown_days = int(cfg.get("proactive.winback.cooldown_days", 45))
        cap = int(cfg.get("proactive.winback.max_per_tick", 20))
        now = datetime.now(UTC)

        # Consent = opted into topic DMs. Quiet = no interaction in N days.
        candidates = list(subs.subs.keys())
        sent = 0
        for chat_id in candidates:
            if sent >= cap:
                break
            if not book.can_nudge(chat_id, cooldown_days):
                continue
            try:
                profile = await self.engine.memory.get_profile(f"telegram:{chat_id}")
            except Exception:  # noqa: BLE001
                continue
            last = getattr(profile, "last_interaction", None)
            if not last or (now - last).days < inactive_days:
                continue
            name = (getattr(profile, "display_name", "") or "").split()[:1]
            hi = f"Hey {name[0]}, " if name else "Hey, "
            msg = (
                f"👋 {hi}it's been a minute! A few things have moved at Stobox since we "
                "last chatted. Want the short version?\n\n"
                "• Ask me anything, anytime — I'm here 24/7\n"
                "• /blog — the latest posts + RWA digest\n"
                "• /subscribe — fine-tune what I ping you about (or turn it off)\n\n"
                "<i>No pressure at all — reply /unsubscribe and I'll go quiet.</i>"
            )
            try:
                await context.bot.send_message(
                    chat_id, msg, parse_mode="HTML", disable_web_page_preview=True
                )
                book.mark_nudged(chat_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001 - user may have blocked the bot
                log.warning("winback.send_failed", chat=chat_id, error=str(exc))
        if sent:
            log.info("winback.nudged", sent=sent, inactive_days=inactive_days)

    async def _resync_job(self, context) -> None:
        try:
            results = await self.engine.sync_knowledge()
        except Exception as exc:  # noqa: BLE001
            log.error("proactive.resync_failed", error=str(exc))
            return
        total = sum(results.values())
        log.info("proactive.resync", results=results, total=total)
        for admin_id in getattr(self.app.bot_data.get("adapter"), "admins", set()):
            try:
                await context.bot.send_message(admin_id, f"🔄 Daily knowledge resync: {total} chunks ({results}).")
            except Exception:  # noqa: BLE001
                pass

    def _known_chats(self) -> set[str]:
        return getattr(self.app.bot_data.get("adapter"), "known_chats", set())

    def _in_quiet_hours(self) -> bool:
        rng = self.engine.config.get("proactive.evangelist.quiet_hours", [0, 7])
        hour = datetime.now(UTC).hour
        return rng[0] <= hour < rng[1]

    async def _evangelist_job(self, context) -> None:
        if self._in_quiet_hours():
            return
        fmt = random.choice(_FORMATS)  # noqa: S311 - not security-sensitive
        if fmt.startswith("Poll"):
            await self._post_poll(context)
            return
        retrieved = await self.engine.retriever.retrieve(fmt)
        context_text = "\n\n".join(rc.chunk.text[:300] for rc in retrieved[:3])
        prompt = self.engine.prompts.render(
            "evangelist_content",
            format=fmt,
            language="en",
            recent="\n".join(self._recent_posts[-5:]) or "(none)",
            context=context_text or "(general Stobox knowledge)",
        )
        system = self.engine.prompts.render(
            "system_base", persona="beginner", mode=Mode.EVANGELIST.value
        )
        try:
            result = await self.engine.reasoner.complete(
                [ChatMessage("system", system), ChatMessage("user", prompt)]
            )
        except Exception as exc:  # noqa: BLE001
            log.error("proactive.evangelist_failed", error=str(exc))
            return
        self._recent_posts.append(fmt)
        for chat_id in self._known_chats():
            try:
                await context.bot.send_message(chat_id, result.text[:4096])
            except Exception:  # noqa: BLE001
                pass
        log.info("proactive.evangelist_posted", format=fmt, chats=len(self._known_chats()))

    async def _post_poll(self, context) -> None:
        """Quiz night — post a native Telegram quiz poll to active groups. Correct
        answers auto-award XP (adapter PollAnswerHandler). Never price/investment."""
        adapter = self.app.bot_data.get("adapter")
        if not adapter:
            return
        posted = 0
        for chat_id in self._known_chats():
            if await adapter.send_quiz(context, chat_id):
                posted += 1
        log.info("proactive.quiz_posted", chats=posted)

    async def _revival_job(self, context) -> None:
        cfg = self.engine.config
        minutes = int(cfg.get("proactive.growth.inactivity_minutes", 240))
        now = datetime.now(UTC)
        for chat_id in self._known_chats():
            thread_key = f"telegram:{chat_id}:main"
            last = self.engine.memory.last_activity(thread_key)
            if last and (now - last).total_seconds() < minutes * 60:
                continue
            retrieved = await self.engine.retriever.retrieve("interesting Stobox fact")
            snippet = retrieved[0].chunk.text[:200] if retrieved else ""
            if not snippet:
                continue
            try:
                await context.bot.send_message(
                    chat_id, f"💡 Did you know? {snippet}\n\nAny questions about this? Ask away!"
                )
                self.engine.memory.add_turn(thread_key, "assistant", "[revival]")
            except Exception:  # noqa: BLE001
                pass

    async def _digest_job(self, context) -> None:
        digest_builder = self.engine.daily_digest()
        digest = digest_builder.build()
        narrative = await digest_builder.narrative(digest)
        text = digest_builder.render_text(digest, narrative)
        for admin_id in getattr(self.app.bot_data.get("adapter"), "admins", set()):
            try:
                await context.bot.send_message(admin_id, text[:4096])
            except Exception:  # noqa: BLE001
                pass
