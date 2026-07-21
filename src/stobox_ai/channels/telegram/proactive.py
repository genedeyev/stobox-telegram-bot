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

import asyncio
import random
import re
from datetime import UTC, datetime
from html import escape as html_escape
from pathlib import Path

from ...core.types import Mode
from ...guardrails.freshness import burn_before_phrase as _burn_before
from ...llm.base import ChatMessage
from ...logging import get_logger

log = get_logger(__name__)

# Where one-shot broadcast dedupe state persists (config: proactive.state_path).
DEFAULT_STATE_PATH = "data/proactive_state.json"


def _parse_hhmm(value, default=(8, 0)) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute); fall back on bad input."""
    try:
        hh, mm = (int(x) for x in str(value).split(":", 1))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except (ValueError, TypeError):
        pass
    return default


async def send_with_flood_control(bot, chat_id, text: str, *, retries: int = 2,
                                  html: bool = False, **kwargs) -> str:
    """Send a message honoring Telegram's flood-wait signal.

    Telegram raises RetryAfter at ~30 msg/s globally (and per-chat limits);
    swallowing it silently drops recipients from a blast. When ``html=True`` the
    message is sent with HTML parse mode and, if Telegram rejects the markup,
    retried once as stripped plain text — so a stray tag never surfaces raw in
    the chat (the `<b>…</b>` bug) nor drops the post entirely. Returns:
      "ok"        — delivered
      "forbidden" — user blocked the bot / bot kicked (caller may unsubscribe)
      "failed"    — other error after retries (caller decides whether to retry later)
    """
    from telegram.error import BadRequest, Forbidden, RetryAfter

    if html:
        kwargs.setdefault("parse_mode", "HTML")
        kwargs.setdefault("disable_web_page_preview", True)

    for _ in range(retries + 1):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return "ok"
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except Forbidden:
            return "forbidden"
        except BadRequest as exc:
            if html:   # bad markup → send stripped plain text, don't lose the post
                kwargs.pop("parse_mode", None)
                try:
                    await bot.send_message(chat_id, _strip_tags(text), **kwargs)
                    return "ok"
                except Exception as e2:  # noqa: BLE001
                    log.warning("send.html_fallback_failed", chat=str(chat_id), error=str(e2))
                    return "failed"
            log.warning("send.failed", chat=str(chat_id), error=str(exc))
            return "failed"
        except Exception as exc:  # noqa: BLE001 - deleted chat, bad id, network…
            log.warning("send.failed", chat=str(chat_id), error=str(exc))
            return "failed"
    return "failed"


# Any HTML-like tag (letter or / after '<'), so the plain-text fallback removes
# unsupported tags too (<h1>, <p>, …) — but leaves prose like "STBU < $1" alone.
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text or "")

_FORMATS = [
    # Hero topics — weighted heavier so proactive content leans into STBU,
    # migration, news/achievements, and the blog.
    "STBU migration reminder (deadline + steps, from FRESHNESS/CANONICALS)",
    "STBU migration step-by-step (burn-and-mint, same wallet, consolidate first)",
    "STBU fact or utility spotlight",
    "Stobox achievement spotlight (clients / assets / jurisdictions, as published)",
    "Latest blog highlight (point to the newest post + the weekly RWA Digest)",
    "News & momentum recap (grounded, no hype)",
    # General rotation.
    "Tip of the day", "Feature spotlight", "RWA education", "Security advice",
    "Tokenization myth-buster", "Product update recap",
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

# Quieter, low-key openers for surfacing an EXISTING post when a chat goes still.
_QUIET_BLOG_OPENERS = [
    "Quiet in here — here's a good read from our blog while it's calm:",
    "In case it's useful, one from the archive worth a look:",
    "Bit slow today — here's a piece some folks found handy:",
    "While things are quiet, a read that comes up a lot:",
    "No rush, but this one's worth bookmarking:",
]

# Open, on-brand conversation starters that INVITE a reply (proactive engagement).
# No links, no pressure — just spark discussion when a room goes still.
_ENGAGE_PROMPTS = [
    # Hero topics first — STBU, migration, news, blog.
    "Quick check 👇 Have you migrated your STBU to Base yet? Burn-and-mint, 1:1, same wallet — "
    "the deadline's 15 Sep. Ask me anything about the steps.",
    "Reminder for STBU holders: consolidate all your STBU into ONE wallet before migrating. "
    "Questions about the Base migration? I'm here for it.",
    "Caught the latest on the Stobox blog? Ask me for the newest post or the weekly RWA & "
    "Tokenization Digest — happy to share.",
    "Milestone moment: Stobox has supported $305M+ in assets across 20+ jurisdictions since "
    "2018. What corner of RWA are you most excited about?",
    "Quick one for the room 👇 If you could tokenize any real-world asset tomorrow, "
    "what would it be?",
    "Curious what brought everyone here — the STBU migration, RWA in general, or building "
    "with Compass?",
    "Honest question: what's the biggest thing holding tokenization back right now — "
    "regulation, liquidity, or awareness?",
    "What would make you trust a tokenized asset enough to actually hold it? 🤔",
    "Issuers in here — what asset class are you eyeing? Real estate, a fund, equity, "
    "private credit?",
    "What's one thing about Stobox or RWA you wish was explained more simply? I'll take a "
    "crack at it.",
    "Building anything on-chain right now? Drop what you're working on — happy to point you "
    "the right way.",
    "Real estate, private credit, or company equity — which do you reckon tokenizes best, "
    "and why?",
]


def migration_status_line(canon, today) -> str | None:
    """One concise, HTML STBU→Base migration status line for `today`, grounded in
    the canonical dates. Reused by the twice-daily updates briefing AND the
    new-member welcome (the single most relevant live update). Returns None when
    the canonical dates are missing. Pure/synchronous — trivially unit-tested."""
    from ...guardrails.canonicals import _as_date

    if canon is None:
        return None
    m = canon.get("tokens.stbu.migration", {}) or {}
    window_open = _as_date(m.get("burn_window_opens")) or _as_date(m.get("burns_count_from"))
    deadline = _as_date(m.get("burn_deadline"))
    claims = _as_date(m.get("claim_opens"))
    if not deadline:
        return None

    def _in(n: int) -> str:
        return "today" if n == 0 else ("tomorrow" if n == 1 else f"in {n} days")

    # Before the window opens → count down to the opening.
    if window_open and today < window_open:
        days = (window_open - today).days
        return (f"⏳ <b>STBU → Base</b> burn window opens <b>{_in(days)}</b> "
                f"({window_open.strftime('%d %b %Y')}). Consolidate all STBU into one "
                "wallet now; 1:1, same wallet only. Steps: /migrate")
    # Window open, on/before the deadline → count down to the deadline.
    if today <= deadline:
        days = (deadline - today).days
        return (f"⏳ <b>STBU → Base migration is OPEN</b> — burn deadline <b>{_in(days)}</b> "
                f"(burn {_burn_before(deadline, '%d %b %Y')}). Burn-and-mint, 1:1, same "
                "wallet only. Steps: /migrate")
    # After the deadline.
    if claims and today >= claims:
        return ("🟢 <b>STBU claims are open on Base</b> — if you burned before the deadline, "
                "claim now (same wallet). Steps: /migrate")
    return ("⛔ The <b>STBU → Base</b> burn window has closed. "
            + (f"Claims open {claims.strftime('%d %b %Y')}. " if claims else "")
            + "Details: /migrate")


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
        # Per-chat rotation of blog posts already surfaced during quiet times.
        self._blog_shared: dict[str, set[str]] = {}
        self._blog_i = 0
        # Per-chat rotation of conversation-starter prompts; alternates with blogs.
        self._prompt_shared: dict[str, set[int]] = {}
        self._prompt_i = 0
        self._revival_i = 0
        # Per-chat revival state: consecutive unanswered nudges + our last nudge's
        # activity timestamp, so we back off from a room nobody's engaging with.
        self._revival_state: dict[str, dict] = {}
        # Migration-countdown dedupe + updates-briefing dedupe. PERSISTED: the
        # one-shot announcements ("window opened", "claims open") must survive
        # restarts, or every redeploy after claims open would re-broadcast them
        # to every group at the next 09:00 tick.
        from ...ops.statefile import load_json_guarded

        cfg = getattr(engine, "config", None)
        self._state_path = Path(
            cfg.get("proactive.state_path", DEFAULT_STATE_PATH)
            if cfg else DEFAULT_STATE_PATH
        )
        state = load_json_guarded(self._state_path, label="proactive") or {}
        # Last date (or "opened"/"claims-open") the public countdown posted.
        self._countdown_last: str = str(state.get("countdown_last", ""))
        # Last updates-briefing text, to skip back-to-back identical broadcasts.
        self._updates_last: str = str(state.get("updates_last", ""))
        # Last evangelist post time (ISO). PERSISTED so the post cadence is
        # driven by wall-clock, not process uptime — otherwise every redeploy
        # reset the "first post in 4h" timer and Stoby went silent for hours.
        self._evangelist_last: str = str(state.get("evangelist_last", ""))
        # Last weekly content-preview time (ISO) — same redeploy-proofing.
        self._content_last: str = str(state.get("content_last", ""))

    def _save_state(self) -> None:
        from ...ops.statefile import save_json_atomic

        try:
            save_json_atomic(self._state_path, {
                "countdown_last": self._countdown_last,
                "updates_last": self._updates_last,
                "evangelist_last": self._evangelist_last,
                "content_last": self._content_last,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("proactive.state_save_failed", error=str(exc))

    def schedule(self) -> None:
        jq = getattr(self.app, "job_queue", None)
        if jq is None:
            log.warning("proactive.no_job_queue", hint="install python-telegram-bot[job-queue]")
            return
        cfg = self.engine.config
        # Liveness heartbeat — the container HEALTHCHECK stats this file's
        # mtime, catching a wedged event loop / dead job queue (an `import`
        # check can't).
        jq.run_repeating(self._heartbeat_job, interval=60, first=5)
        if cfg.get("proactive.evangelist.enabled", True):
            hours = float(cfg.get("proactive.evangelist.interval_hours", 3))
            # first=warmup (default 15 min), NOT the full interval: Stoby engages
            # shortly after boot instead of hours later. The job's own wall-clock
            # gap check (persisted _evangelist_last) prevents redeploy spam.
            warmup = float(cfg.get("proactive.evangelist.warmup_seconds", 900))
            jq.run_repeating(self._evangelist_job, interval=hours * 3600, first=warmup)
        if cfg.get("proactive.growth.revive_inactive", True):
            jq.run_repeating(self._revival_job, interval=1800, first=300)  # check ~5m then every 30m
        if cfg.get("observability.daily_digest", True):
            # run_daily at a FIXED UTC time — NOT first=24h. With first=interval
            # every redeploy reset the timer, so the digest effectively never
            # fired (Arevik: "haven't seen it"). Daily jobs must be wall-clock.
            from datetime import time as _time

            hh, mm = _parse_hhmm(cfg.get("observability.digest_time", "08:00"))
            jq.run_daily(self._digest_job, time=_time(hh, mm, tzinfo=UTC))
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
        # PUBLIC migration countdown to groups — once a day at 09:00 UTC; the job
        # itself decides whether today is a post-day (weekly far out → daily in
        # the final week). Separate from the opt-in /remindme DMs.
        if cfg.get("proactive.migration_countdown.enabled", True):
            from datetime import time as _time

            jq.run_daily(self._migration_countdown_job, time=_time(9, 0, tzinfo=UTC))
        # "What's new at Stobox" — a curated updates briefing (latest blog +
        # migration status + live STBU market) posted to groups on a fixed daily
        # schedule (twice a day by default). Stoby INITIATES with relevant updates.
        updates = cfg.section("proactive.updates")
        if updates.get("enabled", True):
            from datetime import time as _time

            for hhmm in updates.get("times", ["12:00", "18:00"]):
                hh, mm = _parse_hhmm(hhmm, default=(-1, -1))
                if hh < 0:
                    log.warning("proactive.updates_bad_time", value=hhmm)
                    continue
                jq.run_daily(self._updates_briefing_job, time=_time(hh, mm, tzinfo=UTC))
        # Opt-in win-back: check quiet subscribers a few times a day.
        if cfg.get("proactive.winback.enabled", True):
            jq.run_repeating(self._winback_job, interval=6 * 3600, first=1800)
        # Weekly content-ideas preview to admins (from community question-gaps).
        if cfg.get("proactive.content.enabled", True):
            # Weekly admin content preview — also wall-clock (run_daily; the job
            # self-gates to run once a week) so redeploys don't keep resetting it.
            from datetime import time as _time

            hh, mm = _parse_hhmm(cfg.get("proactive.content.time", "08:30"))
            jq.run_daily(self._content_job, time=_time(hh, mm, tzinfo=UTC))
        log.info("proactive.scheduled")

    async def _heartbeat_job(self, context) -> None:
        import os

        try:
            Path(os.environ.get("HEARTBEAT_FILE", "/tmp/stobox-heartbeat")).touch()
        except OSError:  # pragma: no cover - read-only fs etc.
            pass

    async def _content_job(self, context) -> None:
        """DM admins a weekly preview of blog outlines drafted from question-gaps.
        Runs on the daily tick but self-gates to once every 7 days (wall-clock,
        persisted) so it survives redeploys."""
        adapter = self.app.bot_data.get("adapter")
        if not adapter or not getattr(adapter, "admins", None):
            return
        now = datetime.now(UTC)
        if self._content_last:
            try:
                if (now - datetime.fromisoformat(self._content_last)).total_seconds() < 7 * 86400 * 0.9:
                    return
            except ValueError:
                pass
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
        await adapter.dm_admins(context, "\n".join(lines))
        self._content_last = now.isoformat()
        self._save_state()
        log.info("content.previewed", count=len(results))

    @staticmethod
    def _countdown_due(days: int, today) -> bool:
        """Ramping cadence: daily in the final week, ~every 3 days within a month,
        weekly (Mondays) when the deadline is further out."""
        if days <= 7:
            return True
        if days <= 30:
            return days % 3 == 0
        return today.weekday() == 0

    async def _migration_countdown_job(self, context) -> None:
        """Public STBU→Base countdown to groups (separate from opt-in /remindme)."""
        from ...guardrails.canonicals import _as_date

        canon = self.engine.assembler.canonicals if self.engine.assembler else None
        chats = self._known_chats()
        if not canon or not chats:
            return
        m = canon.get("tokens.stbu.migration", {}) or {}
        window_open = _as_date(m.get("burn_window_opens")) or _as_date(m.get("burns_count_from"))
        deadline = _as_date(m.get("burn_deadline"))
        claims = _as_date(m.get("claim_opens"))
        today = datetime.now(UTC).date()
        if not deadline:
            return

        # Opening day → one "window is NOW OPEN" announcement.
        if window_open and today == window_open and self._countdown_last != "opened":
            self._countdown_last = "opened"
            self._save_state()
            text = ("🟢 <b>The STBU → Base burn window is NOW OPEN!</b>\n\n"
                    "Burn-and-mint, 1:1, <b>same wallet only</b> — consolidate all your STBU into "
                    "one wallet first. Legacy V1 isn't eligible. Burn "
                    f"{_burn_before(deadline, '%d %b %Y')} — minting opens the same instant.\n\n"
                    "Steps: /migrate  ·  Reminders: /remindme\n"
                    "⚠️ Stobox staff never DM you first; only trust links from stobox.io.")
            await self._broadcast(context, chats, text)
            log.info("migration.window_opened", chats=len(chats))
            return

        # Before the window opens → count down to the OPENING (build the hype).
        if window_open and today < window_open:
            days = (window_open - today).days
            if self._countdown_last == str(today) or not self._countdown_due(days, today):
                return
            when = ("<b>TODAY</b>" if days == 0 else
                    "<b>tomorrow</b>" if days == 1 else f"in <b>{days} days</b>")
            text = (
                f"🔥 The <b>STBU → Base</b> burn window <b>OPENS {when}</b> — "
                f"{window_open.strftime('%d %b %Y')}!\n\n"
                "Get ready: consolidate all your STBU into <b>one wallet</b> now "
                "(burn-and-mint will be 1:1, same wallet only). Legacy V1 isn't eligible.\n\n"
                "Steps: /migrate  ·  Reminders: /remindme\n"
                "⚠️ Stobox staff never DM you first; only trust links from stobox.io."
            )
            await self._broadcast(context, chats, text)
            self._countdown_last = str(today)
            self._save_state()
            log.info("migration.preopen_posted", days=days, chats=len(chats))
            return

        # After the deadline → one "claims open" announcement, then done.
        if today > deadline:
            if claims and today >= claims and self._countdown_last != "claims-open":
                self._countdown_last = "claims-open"
                self._save_state()
                text = ("🟢 <b>STBU claims are open.</b> If you burned before the deadline, "
                        "you can now claim on Base (same wallet). Steps: /migrate · stobox.io\n"
                        "⚠️ Stobox staff never DM you first; only trust links from stobox.io.")
                await self._broadcast(context, chats, text)
                log.info("migration.claims_announced", chats=len(chats))
            return

        days = (deadline - today).days
        if self._countdown_last == str(today) or not self._countdown_due(days, today):
            return
        when = ("<b>TODAY</b>" if days == 0 else
                "<b>tomorrow</b>" if days == 1 else f"in <b>{days} days</b>")
        text = (
            f"⏳ The <b>STBU → Base</b> burn deadline is {when} — burn "
            f"{_burn_before(deadline, '%d %b %Y')} (minting opens the same instant).\n\n"
            "Burn-and-mint, 1:1, <b>same wallet only</b>. Consolidate all your STBU into one "
            "wallet first; legacy V1 isn't eligible.\n\n"
            "Steps: /migrate  ·  Personal reminders: /remindme\n"
            "⚠️ Stobox staff never DM you first; only trust links from stobox.io."
        )
        await self._broadcast(context, chats, text)
        self._countdown_last = str(today)
        self._save_state()
        log.info("migration.countdown_posted", days=days, chats=len(chats))

    async def _build_updates_briefing(self) -> str | None:
        """Compose the 'What's new at Stobox' briefing from the enabled sources
        (migration status + live STBU market + latest blog). Returns None when
        nothing substantive resolves. Never raises — a broken source is skipped."""
        cfg = self.engine.config.section("proactive.updates")
        blocks: list[str] = []
        today = datetime.now(UTC).date()

        # 1) Migration status — the single most time-sensitive update. Skip it when
        #    the dedicated public countdown already posted a dated reminder to these
        #    same groups today, so migration isn't announced twice in one day.
        if cfg.get("include_migration", True) and self._countdown_last != str(today):
            canon = self.engine.assembler.canonicals if self.engine.assembler else None
            line = migration_status_line(canon, today)
            if line:
                blocks.append(line)

        # 2) Live STBU market snapshot (grounded fact, framed as not-advice).
        if cfg.get("include_market", True):
            try:
                snap = await self.engine.market_snapshot()
            except Exception:  # noqa: BLE001
                snap = None
            if snap:
                blocks.append(
                    f"📈 <b>STBU</b>: {snap.format_brief()} — market data, "
                    "not advice, not the company valuation."
                )

        # 3) Latest blog / news post.
        if cfg.get("include_blog", True):
            posts = getattr(self.engine, "blog_posts", None) or []
            if posts:
                p = posts[0]
                blocks.append(f"📰 <b>Latest read</b>: {html_escape(p['title'])}\n{p['url']}")

        if not blocks:
            return None
        header = "📣 <b>What's new at Stobox</b>"
        footer = ("\n\nQuestions on any of this? Just ask — I'm here 24/7. "
                  "⚠️ Stobox staff never DM you first; only trust links from stobox.io.")
        return header + "\n\n" + "\n\n".join(blocks) + footer

    async def _updates_briefing_job(self, context) -> None:
        """Post the curated updates briefing to community groups (twice daily)."""
        if self._in_quiet_hours():
            return
        chats = self._known_chats()
        if not chats:
            return
        text = await self._build_updates_briefing()
        if not text:
            log.info("updates.nothing_to_post")
            return
        # Skip if identical to the last briefing (e.g. market feed down + same
        # blog + same migration day → the two daily slots would be duplicates).
        if text == self._updates_last:
            log.info("updates.skipped_duplicate")
            return
        await self._broadcast(context, chats, text)
        self._updates_last = text
        self._save_state()
        log.info("updates.briefing_posted", chats=len(chats))

    async def _broadcast(self, context, chats, text: str) -> None:
        ok = failed = 0
        for chat_id in chats:
            status = await send_with_flood_control(
                context.bot, chat_id, text,
                parse_mode="HTML", disable_web_page_preview=True,
            )
            ok += status == "ok"
            failed += status != "ok"
            await asyncio.sleep(0.05)   # stay well under Telegram's global send rate
        if failed:
            log.warning("broadcast.partial", delivered=ok, failed=failed)

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
                    f"<b>{when}</b>: burn {_burn_before(deadline, '%d %B %Y')}.\n\n"
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
        sent = failed = 0
        for chat_id in list(book.subscribers):
            if book.was_delivered(tag, chat_id):
                continue    # already got this blast (crash/flood-wait retry pass)
            status = await send_with_flood_control(
                context.bot, chat_id, text,
                parse_mode="HTML", disable_web_page_preview=True,
            )
            if status == "ok":
                book.mark_delivered(tag, chat_id)
                sent += 1
            elif status == "forbidden":
                # Blocked the bot — that's an unsubscribe, not a retry-forever.
                book.unsubscribe(chat_id)
                log.info("reminders.unsubscribed_blocked", chat=chat_id)
            else:
                failed += 1
            await asyncio.sleep(0.05)
        # Only close the tag after a pass with zero transient failures — the
        # hourly tick retries just the missed subscribers otherwise.
        if failed == 0:
            book.mark_sent(tag)
        log.info("reminders.blast", tag=tag, sent=sent, failed=failed,
                 subscribers=len(book.subscribers))

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
            # Crawled OG metadata is untrusted for HTML mode — escape it.
            caption = f"{opener}\n\n<b>{html_escape(title)}</b>"
            if teaser:
                caption += f"\n{html_escape(teaser[:220])}"
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
        # Title/teaser come from crawled OG metadata — escape before HTML mode.
        body = f"<b>{html_escape(title)}</b>"
        if teaser:
            body += f"\n{html_escape(teaser[:220])}"
        dm = (f"{label} — new from Stobox 👇\n\n{body}\n\n🔗 {url}"
              f"\n\n<i>You're subscribed to {label}. Manage or stop with /subscribe.</i>")
        sent = 0
        for chat_id, _lang in recipients:
            status = await send_with_flood_control(
                context.bot, chat_id, dm[:4096],
                parse_mode="HTML", disable_web_page_preview=False,
            )
            if status == "ok":
                sent += 1
            await asyncio.sleep(0.05)
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
        # Nightly housekeeping rides the same 04:00 tick: decision-log retention
        # (rows carry verbatim member questions — PII must not grow forever).
        try:
            days = int(self.engine.config.get("observability.decision_retention_days", 90))
            await self.engine.decisions.prune(days)
        except Exception as exc:  # noqa: BLE001
            log.warning("proactive.decision_prune_failed", error=str(exc))
        try:
            results = await self.engine.sync_knowledge()
        except Exception as exc:  # noqa: BLE001
            log.error("proactive.resync_failed", error=str(exc), exc_info=True)
            # A silently-missed resync leaves the corpus a day stale until the
            # next 04:00 tick. Alert admins and retry in 30 minutes (transient
            # network/API failures are the common case) — max 3 retries per day
            # so a real outage doesn't loop-and-ping forever.
            self._resync_failures = getattr(self, "_resync_failures", 0) + 1
            adapter = self.app.bot_data.get("adapter")
            jq = getattr(self.app, "job_queue", None)
            if self._resync_failures <= 3:
                if adapter:
                    await adapter.dm_admins(
                        context,
                        f"⚠️ Daily knowledge resync FAILED ({str(exc)[:150]}). "
                        f"Retry {self._resync_failures}/3 in 30 min — or run /sync now.")
                if jq is not None:
                    jq.run_once(self._resync_job, when=1800)
            elif adapter:
                await adapter.dm_admins(
                    context,
                    "⛔ Knowledge resync failed 3 retries — giving up until the next "
                    "04:00 UTC run. The corpus is marked stale in [FRESHNESS]; "
                    "run /sync once the underlying issue is fixed.")
            return
        self._resync_failures = 0
        total = sum(results.values())
        log.info("proactive.resync", results=results, total=total)
        adapter = self.app.bot_data.get("adapter")
        if adapter:
            await adapter.dm_admins(
                context, f"🔄 Daily knowledge resync: {total} chunks ({results}).")

    def _known_chats(self) -> set[str]:
        # COPY, not the adapter's live set: jobs iterate this across awaits while
        # handlers may concurrently add chats ("set changed size during iteration").
        chats = set(getattr(self.app.bot_data.get("adapter"), "known_chats", ()))
        # Arevik: post ONLY to the community group. If group_ids is configured,
        # restrict to those; otherwise every known group (channels never enter
        # known_chats, so Ross's announcement channel is already excluded).
        allow = {str(c) for c in (self.engine.config.get("proactive.group_ids") or [])}
        return (chats & allow) if allow else chats

    def _in_quiet_hours(self) -> bool:
        rng = self.engine.config.get("proactive.evangelist.quiet_hours", [0, 7])
        hour = datetime.now(UTC).hour
        return rng[0] <= hour < rng[1]

    async def _evangelist_job(self, context) -> None:
        if self._in_quiet_hours():
            return
        # Wall-clock gap guard: skip if we already posted within ~90% of the
        # interval (a redeploy fired the warmup timer again). Uptime-independent.
        hours = float(self.engine.config.get("proactive.evangelist.interval_hours", 3))
        now = datetime.now(UTC)
        if self._evangelist_last:
            try:
                last = datetime.fromisoformat(self._evangelist_last)
                if (now - last).total_seconds() < hours * 3600 * 0.9:
                    return
            except ValueError:
                pass
        if not self._known_chats():
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
        # Use the assembled [CORE]+[CANONICALS]+[FRESHNESS] prompt so proactive
        # posts are grounded in the same canonical facts as replies (split so
        # the stable prefix prompt-caches); legacy system_base is the fallback.
        sys_msgs = self.engine.system_messages() or [ChatMessage(
            "system",
            self.engine.prompts.render(
                "system_base", persona="beginner", mode=Mode.EVANGELIST.value
            ),
        )]
        try:
            result = await self.engine.reasoner.complete(
                [*sys_msgs, ChatMessage("user", prompt)]
            )
        except Exception as exc:  # noqa: BLE001
            log.error("proactive.evangelist_failed", error=str(exc), exc_info=True)
            return
        # PUBLIC output ⇒ same deterministic compliance rails as the reply path.
        # A post asserting a forbidden claim is dropped, never broadcast.
        rail = self.engine.rails.post_process(result.text, "")
        if rail.blocked:
            log.error("proactive.evangelist_blocked", violations=rail.violations)
            return
        self._recent_posts.append(fmt)
        chats = self._known_chats()
        for chat_id in chats:
            await send_with_flood_control(context.bot, chat_id, rail.text[:4096], html=True)
            await asyncio.sleep(0.05)
        self._evangelist_last = now.isoformat()
        self._save_state()
        log.info("proactive.evangelist_posted", format=fmt, chats=len(chats))

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
        """When a chat goes quiet, surface a real blog post (preferred) or an
        interesting fact — never at night, and marked as activity so the same
        chat isn't revived again until it's quiet for another full window."""
        if self._in_quiet_hours():
            return
        cfg = self.engine.config
        minutes = int(cfg.get("proactive.growth.inactivity_minutes", 240))
        max_unanswered = int(cfg.get("proactive.growth.max_unanswered_revivals", 2))
        now = datetime.now(UTC)
        for chat_id in self._known_chats():
            thread_key = f"telegram:{chat_id}:main"
            last = self.engine.memory.last_activity(thread_key)
            if last and (now - last).total_seconds() < minutes * 60:
                continue
            # Back-off: if our last nudge got no human reply, count it. After
            # max_unanswered silent nudges, stay dormant until someone speaks
            # (a human turn moves last_activity past our nudge → streak resets).
            st = self._revival_state.setdefault(chat_id, {"streak": 0, "at": None})
            ignored = st["at"] is not None and last is not None and last <= st["at"]
            st["streak"] = st["streak"] + 1 if ignored else 0
            if st["streak"] >= max_unanswered:
                continue
            revived = await self._revival_content(context, chat_id)
            if revived:
                self.engine.memory.add_turn(thread_key, "assistant", "[revival]")
                st["at"] = self.engine.memory.last_activity(thread_key)

    async def _revival_content(self, context, chat_id) -> bool:
        """Alternate a real blog post and a conversation-starter prompt (both
        engaging); fall back to a grounded fact if neither is available."""
        order = (["blog", "prompt"] if self._revival_i % 2 == 0 else ["prompt", "blog"])
        self._revival_i += 1
        for kind in order:
            if kind == "blog" and await self._share_blog(context, chat_id):
                return True
            if kind == "prompt" and await self._share_prompt(context, chat_id):
                return True
        return await self._share_fact(context, chat_id)

    async def _share_prompt(self, context, chat_id) -> bool:
        """Post an open conversation starter that invites a reply (rotates)."""
        seen = self._prompt_shared.setdefault(chat_id, set())
        fresh = [i for i in range(len(_ENGAGE_PROMPTS)) if i not in seen]
        if not fresh:                       # asked them all → start the cycle over
            seen.clear()
            fresh = list(range(len(_ENGAGE_PROMPTS)))
        idx = fresh[self._prompt_i % len(fresh)]
        self._prompt_i += 1
        try:
            await context.bot.send_message(chat_id, _ENGAGE_PROMPTS[idx])
            seen.add(idx)
            log.info("revival.prompt_shared", chat=chat_id, idx=idx)
            return True
        except Exception as exc:  # noqa: BLE001 - blocked / no rights
            log.warning("revival.prompt_failed", chat=chat_id, error=str(exc))
            return False

    async def _share_blog(self, context, chat_id) -> bool:
        """Surface an existing blog post the chat hasn't seen yet (rotates)."""
        posts = self.engine.all_blog_posts()
        if not posts:
            return False
        seen = self._blog_shared.setdefault(chat_id, set())
        fresh = [p for p in posts if p["url"] not in seen]
        if not fresh:                       # cycled through them all → start over
            seen.clear()
            fresh = posts
        post = fresh[self._blog_i % len(fresh)]
        self._blog_i += 1
        og = await fetch_og_meta(post["url"])
        title = og.get("title") or post["title"]
        teaser = (og.get("description") or "").strip()
        opener = _QUIET_BLOG_OPENERS[self._opener_i % len(_QUIET_BLOG_OPENERS)]
        self._opener_i += 1
        caption = f"{opener}\n\n<b>{title}</b>"
        if teaser:
            caption += f"\n{teaser[:180]}"
        caption += f"\n\n{post['url']}"
        try:
            await context.bot.send_message(chat_id, caption[:1024], parse_mode="HTML")
            seen.add(post["url"])
            log.info("revival.blog_shared", chat=chat_id, url=post["url"])
            return True
        except Exception as exc:  # noqa: BLE001 - blocked / no rights
            log.warning("revival.blog_failed", chat=chat_id, error=str(exc))
            return False

    async def _share_fact(self, context, chat_id) -> bool:
        """Fallback when there are no blog posts yet: a grounded 'did you know'."""
        retrieved = await self.engine.retriever.retrieve("interesting Stobox fact")
        snippet = retrieved[0].chunk.text[:200] if retrieved else ""
        if not snippet:
            return False
        try:
            await context.bot.send_message(
                chat_id, f"💡 Did you know? {snippet}\n\nAny questions about this? Ask away!"
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _digest_job(self, context) -> None:
        digest_builder = self.engine.daily_digest()
        digest = digest_builder.build()
        narrative = await digest_builder.narrative(digest)
        text = digest_builder.render_text(digest, narrative)
        adapter = self.app.bot_data.get("adapter")
        if adapter:
            await adapter.dm_admins(context, text[:4096])
