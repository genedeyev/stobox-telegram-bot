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
        log.info("proactive.scheduled")

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
                "\n\nGive it a read — and if anything sparks a question, ask me right here. 👇"
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
