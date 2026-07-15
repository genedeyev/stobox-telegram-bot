"""AgentEngine — the reusable, channel-agnostic brain.

Pipeline for every inbound message:
    moderate → remember → route → retrieve → synthesize → confidence-gate →
    cite → lead-handle → log.

Nothing here knows about Telegram. A channel adapter feeds it an
``IncomingMessage`` and renders the returned ``AgentResponse``. This is the seam
that lets Discord/Slack/web-widget reuse 100% of the reasoning.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from ..agents.confidence import ConfidenceEngine
from ..agents.router import IntentRouter, Routing
from ..analytics.logger import Decision, DecisionLog, build_decision_log
from ..config import Config
from ..knowledge.indexer import Indexer
from ..knowledge.models import RetrievedChunk
from ..knowledge.retrieval import HybridRetriever
from ..leads.qualifier import LeadQualifier
from ..llm import build_classifier, build_reasoner
from ..llm.base import ChatMessage, LLMProvider
from ..logging import get_logger
from ..memory import MemoryStore, build_memory_store
from ..memory.models import UserProfile
from ..moderation import Moderator
from ..prompts import PromptLibrary, get_prompts
from .types import (
    AgentResponse,
    Author,
    ChatType,
    Citation,
    Confidence,
    IncomingMessage,
    Mode,
)

log = get_logger(__name__)

_IDK = {
    "en": "Honestly? I don't have a solid answer to that yet — and I'd rather flag it "
          "to the Stobox team than guess. I've done exactly that, and I'll follow up "
          "right here as soon as they confirm the answer. 🙌",
    "ru": "Честно? У меня пока нет надёжного ответа — и я лучше передам вопрос команде "
          "Stobox, чем буду гадать. Уже передал(а) — вернусь сюда с ответом, как только "
          "команда подтвердит. 🙌",
    "uk": "Чесно? У мене поки немає надійної відповіді — і я краще передам питання команді "
          "Stobox, ніж гадатиму. Вже передав — повернуся сюди з відповіддю, щойно команда "
          "підтвердить. 🙌",
}


class AgentEngine:
    def __init__(
        self,
        config: Config,
        reasoner: LLMProvider,
        classifier: LLMProvider,
        retriever: HybridRetriever,
        memory: MemoryStore,
        moderator: Moderator,
        leads: LeadQualifier,
        decision_log: DecisionLog,
        prompts: PromptLibrary,
        indexer: Indexer,
    ) -> None:
        self.config = config
        self.reasoner = reasoner
        self.classifier = classifier
        self.router = IntentRouter(classifier)
        self.retriever = retriever
        self.memory = memory
        self.moderator = moderator
        self.strikes = moderator.strikes
        self.leads = leads
        self.decisions = decision_log
        self.prompts = prompts
        self.indexer = indexer
        self.confidence = ConfidenceEngine(
            threshold=float(config.get("confidence.threshold", 0.55)),
            require_citations=bool(config.get("confidence.require_citations", True)),
        )
        self.max_reply = int(config.get("limits.max_reply_chars", 3500))
        # Operational safety: rate limiting + spend cap + kill switch (§7).
        from ..ops import RateLimiter

        self.rate_limiter = RateLimiter(
            per_minute=int(config.get("limits.per_user_messages_per_minute", 20)),
            per_day=int(config.get("limits.per_user_messages_per_day", 100)),
            global_daily_output_tokens=config.get("limits.global_daily_output_tokens", 2_000_000),
        )
        self.paused = False
        self.pause_reason = ""
        # Unanswered-question loop: capture → notify admins → /answer → deliver.
        from ..qa import QARegister

        self.qa = QARegister(config.get("qa.state_path", "data/qa_register.json"))
        # Opt-in migration reminders (/remindme).
        from ..ops.reminders import ReminderBook

        self.reminders = ReminderBook(config.get("reminders.state_path", "data/reminders.json"))
        # Opt-in topic subscriptions (/subscribe migration|rwa-news|product).
        from ..ops.subscriptions import SubscriptionBook

        self.subscriptions = SubscriptionBook(
            config.get("subscriptions.state_path", "data/subscriptions.json")
        )
        # Win-back nudges for quiet, opted-in members (opt-in only, cooldowned).
        from ..ops.winback import WinBackBook

        self.winback = WinBackBook(config.get("winback.state_path", "data/winback.json"))
        # Email follow-up (SMTP env-gated; degrades to CRM lead if unconfigured).
        from ..ops.email import EmailSender

        self.email = EmailSender()
        # Engagement: XP / streaks / leaderboard.
        from ..engagement import AMABook, XPBook

        self.xp = XPBook(config.get("engagement.xp_path", "data/xp.json"))
        self.ama = AMABook(config.get("engagement.ama_path", "data/ama.json"))
        # Latest blog/learn posts discovered in the index (feeds [FRESHNESS] + /blog).
        self.blog_posts: list[dict] = []
        self._blog_index: dict[str, str] = {}          # url -> title (all known posts)
        self._announced_blog: set[str] | None = None   # None = not yet baselined
        # Compliance guardrails (three-block prompt + deterministic rails).
        from ..guardrails import ComplianceRails, PromptAssembler

        self.rails = ComplianceRails()
        self.assembler: PromptAssembler | None = None
        try:
            self.assembler = PromptAssembler.load(
                config.get("guardrails.system_prompt", "SYSTEM-PROMPT.md"),
                config.get("guardrails.canonicals", "canonicals.yaml"),
            )
            log.info("guardrails.loaded", canon_version=self.assembler.canonicals.version)
        except FileNotFoundError as exc:
            log.warning("guardrails.unavailable", error=str(exc))
        self.last_sync: datetime | None = None

    # ------------------------------------------------------------------ #
    @classmethod
    async def create(cls, config: Config) -> AgentEngine:
        reasoner = build_reasoner(config)
        classifier = build_classifier(config)
        indexer = await Indexer.create(config)
        # Warm the index from docs on boot (incremental).
        await indexer.index_directory(config.get("knowledge.docs_path", "docs"))
        # Optionally pull remote sources (stobox.io + GitHub) at startup.
        import os as _os

        sync_on_boot = bool(config.get("knowledge.sync_on_boot", False)) or (
            _os.environ.get("STOBOX_SYNC_ON_BOOT", "").lower() in ("1", "true", "yes")
        )
        if sync_on_boot:
            from ..knowledge.sync import sync_sources

            try:
                await sync_sources(indexer, config)
            except Exception as exc:  # noqa: BLE001 - never block boot on a crawl
                log.error("boot.sync_failed", error=str(exc))
        retriever = HybridRetriever(indexer.store, indexer.embedder, config, reasoner)
        memory = await build_memory_store(config)
        from ..moderation import StrikeBook

        strikes = StrikeBook(
            config.get("moderation.strikes_path", "data/strikes.json"),
            decay_days=int(config.get("moderation.strike_decay_days", 30)),
        )
        moderator = Moderator(config, classifier, strikes)
        leads = LeadQualifier(config)
        decision_log = await build_decision_log(config)
        engine = cls(
            config, reasoner, classifier, retriever, memory, moderator,
            leads, decision_log, get_prompts(), indexer,
        )
        engine.last_sync = datetime.now(UTC)
        await engine.refresh_blog_posts()
        return engine

    # ------------------------------------------------------------------ #
    def daily_digest(self):
        """Proactive Intelligence: a DailyDigest bound to this engine's log."""
        from ..insights import DailyDigest

        return DailyDigest(self.decisions, self.reasoner)

    def weekly_faq(self):
        """Proactive Intelligence: a WeeklyFAQ generator bound to this engine."""
        from ..insights import WeeklyFAQ

        return WeeklyFAQ(
            self.decisions, self.retriever, self.reasoner, self.prompts,
            confidence_threshold=self.confidence.threshold,
        )

    # ------------------------------------------------------------------ #
    def pause(self, reason: str = "") -> None:
        self.paused = True
        self.pause_reason = reason
        log.warning("engine.paused", reason=reason)

    def resume(self) -> None:
        self.paused = False
        self.pause_reason = ""
        log.info("engine.resumed")

    def _static_faq(self) -> str:
        """Answer used while paused / over the global cap — no LLM."""
        canon = self.assembler.canonicals if self.assembler else None
        support = canon.get("official_links.support_email", "support@stobox.io") if canon else "support@stobox.io"
        site = canon.get("official_links.website", "https://stobox.io") if canon else "https://stobox.io"
        return (
            "I'm temporarily limited to essential info right now. For the latest, see "
            f"{site}. Holder/support questions: {support}. "
            "Stobox staff never DM you first — verify links with /sources."
        )

    def _static_response(self, msg: IncomingMessage, text: str, meta_key: str) -> AgentResponse:
        return AgentResponse(
            text=text[: self.max_reply],
            confidence=Confidence.LOW,
            mode=Mode.COMMUNITY_MANAGER,
            language="en",
            reply_to_message_id=msg.message_id,
            meta={meta_key: True},
        )

    async def detailed_answer(self, question: str, user_key: str = "followup") -> AgentResponse:
        """Full, comprehensive answer for a topic — used by the 'More detail'
        button and the email follow-up. Runs the normal pipeline in detail mode."""
        msg = IncomingMessage(
            author=Author(external_id=user_key.split(":")[-1], channel="telegram"),
            text=question, chat_id=f"detail:{user_key}", chat_type=ChatType.PRIVATE,
            message_id="0", channel="telegram", raw={"addressed": True, "detail": True},
        )
        resp = await self.handle(msg)
        return resp or AgentResponse(text="I don't have more detail on that yet.")

    async def generate_quiz(self) -> dict | None:
        """Produce a grounded multiple-choice quiz: {question, options,
        correct_index, explanation}. Never about price/investment. Returns None
        on any doubt (never posts junk)."""
        from ..util import extract_json

        retrieved = await self.retriever.retrieve("tokenization RWA STV3 compliance education")
        ctx = "\n\n".join(rc.chunk.text[:250] for rc in retrieved[:4])
        prompt = (
            "Create ONE multiple-choice quiz question about RWA tokenization or Stobox, "
            "grounded ONLY in this context. Educational and fun — NEVER about price, "
            "investment, or predictions. Return ONLY minified JSON: "
            '{"question":"...max 250 chars","options":["..","..","..","..],'
            '"correct_index":0,"explanation":"one sentence, max 180 chars"}. '
            "Exactly 4 options (max 90 chars each), one clearly correct.\n\n"
            f"Context:\n{ctx}"
        )
        try:
            raw = await self.reasoner.complete_json([ChatMessage("user", prompt)], max_tokens=350)
            q = extract_json(raw)
            question = str(q["question"])[:250]
            options = [str(o)[:100] for o in q["options"]][:4]
            ci = int(q["correct_index"])
            explanation = str(q.get("explanation", ""))[:190]
            if len(options) == 4 and 0 <= ci < 4:
                return {"question": question, "options": options,
                        "correct_index": ci, "explanation": explanation}
        except Exception as exc:  # noqa: BLE001
            log.warning("quiz.generate_failed", error=str(exc))
        return None

    async def check_wallet(self, address: str) -> str:
        """Read STBU balances for a PUBLIC address across the eligible chains and
        return a ready-to-send migration report (HTML). Read-only, no keys."""
        from ..chain import WalletChecker, is_address, is_private_key
        from ..guardrails.freshness import compute_migration_phase
        from ..guardrails.rails import IMPERSONATION_WARNING

        if is_private_key(address):
            return (
                "🚨 That looks like a <b>private key</b>, not a wallet address — never share "
                "it with anyone, including me. If you've posted it, consider that wallet "
                "compromised and move your funds to a new wallet immediately."
            )
        if not is_address(address):
            return ("That doesn't look like a wallet address. Paste a public address that "
                    "starts with <code>0x</code> (42 characters) and I'll check it.")
        canon = self.assembler.canonicals if self.assembler else None
        contracts = canon.get("tokens.stbu.migration.eligible_contracts", {}) if canon else {}
        if not contracts:
            return "I can't check balances right now — please see stobox.io for migration help."
        rpc = self.config.section("chain.rpc").raw or {}
        checker = WalletChecker(contracts, rpc=rpc)
        try:
            holdings = await checker.check(address)
        except Exception as exc:  # noqa: BLE001
            log.error("chain.check_failed", error=str(exc))
            return "I couldn't reach the chains just now — please try again shortly."

        held = [h for h in holdings if h.ok and h.balance > 0]
        errored = [h for h in holdings if not h.ok]
        short = f"{address[:6]}…{address[-4:]}"
        lines = [f"🔎 <b>STBU check for {short}</b>"]
        if held:
            for h in sorted(held, key=lambda x: -x.balance):
                lines.append(f"• {h.label}: <b>{h.balance:,.2f} STBU</b>")
            phase = compute_migration_phase(canon)[1] if canon else ""
            lines.append("")
            lines.append("<b>Your migration path:</b>")
            lines.append("1. Consolidate all STBU into ONE wallet you control (self-custody).")
            lines.append("2. Burn-and-mint 1:1 to <b>Base</b>, same wallet — steps: /migrate")
            if phase:
                lines.append(f"3. {phase}")
            lines.append("\nLegacy V1 tokens are not eligible.")
            lines.append("\n" + IMPERSONATION_WARNING)
        else:
            lines.append("No STBU found on the eligible chains for this address.")
            lines.append("If you hold STBU on an exchange (e.g. MEXC), withdraw it to a "
                         "self-custody wallet first, then migrate. Full steps: /migrate")
        if errored:
            lines.append(f"\n(Couldn't reach: {', '.join(h.label for h in errored)} — try again.)")
        return "\n".join(lines)

    async def draft_answer(self, question: str) -> str:
        """Best-effort PROPOSED answer for an unanswered question, for admin
        review only (never sent to users). Grounded in retrieval + canonicals;
        returns "" when there's nothing solid to draft from."""
        retrieved = await self.retriever.retrieve(question)
        context, _ = self._format_context(retrieved)
        system = self.system_prompt() or ""
        prompt = (
            "An admin will REVIEW this — it is NOT sent to users. Draft the best "
            "canonical answer to the community question below, grounded ONLY in "
            "your [CANONICALS] facts and the documentation context. Match the "
            "register's tone: professional, calm, factual, correct false premises "
            "politely. 2-6 sentences. If the grounding is insufficient for a "
            "defensible draft, output exactly NO_DRAFT.\n\n"
            f"Question: {question}\n\n"
            f"Documentation context:\n{context or '(none retrieved)'}"
        )
        try:
            result = await self.reasoner.complete(
                [ChatMessage("system", system), ChatMessage("user", prompt)],
                max_tokens=400,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("qa.draft_failed", error=str(exc))
            return ""
        text = result.text.strip()
        return "" if ("NO_DRAFT" in text or len(text) < 20) else text

    async def sync_knowledge(self) -> dict[str, int]:
        """Crawl stobox.io + ingest the GitHub repos into the live index."""
        from ..knowledge.sync import sync_sources

        results = await sync_sources(self.indexer, self.config)
        self.last_sync = datetime.now(UTC)
        await self.refresh_blog_posts()
        return results

    async def refresh_blog_posts(self, limit: int = 5) -> None:
        """Collect the freshest blog/digest URLs from the index for [FRESHNESS]
        and /blog. Best-effort — empty until a web sync has run."""
        try:
            chunks = await self.retriever.store.all_chunks()
        except Exception:  # noqa: BLE001
            return
        seen: dict[str, str] = {}
        for c in chunks:
            url = c.meta.source_url if c.meta else None
            if url and "/blog" in url and url.rstrip("/") != "https://www.stobox.io/blog":
                seen.setdefault(url, c.meta.title)
        self._blog_index = seen
        self.blog_posts = [{"title": t, "url": u} for u, t in list(seen.items())[:limit]]

    def pop_new_blog_posts(self) -> list[dict]:
        """New posts since the last check. The FIRST call after boot baselines
        the current index and returns [] — so a restart never re-announces old
        posts. Callers must mark_blog_announced() after a successful post."""
        current = self._blog_index
        if self._announced_blog is None:
            # Don't baseline an empty index (boot sync may still be running) —
            # wait for the first sync that actually finds posts.
            if not current:
                return []
            self._announced_blog = set(current)
            log.info("blog.baseline", known_posts=len(current))
            return []
        new = [u for u in current if u not in self._announced_blog]
        return [{"url": u, "title": current[u]} for u in new]

    def mark_blog_announced(self, url: str) -> None:
        if self._announced_blog is None:
            self._announced_blog = set()
        self._announced_blog.add(url)

    def build_freshness(self) -> str:
        """Assemble the live [FRESHNESS] block for the current request."""
        from ..guardrails import FreshnessBuilder

        canon = self.assembler.canonicals if self.assembler else None
        if canon is None:
            return ""
        corpus_hash = f"v{self.retriever.store.version}"
        return FreshnessBuilder(
            canon=canon,
            last_sync=self.last_sync,
            corpus_hash=corpus_hash,
            blog_posts=self.blog_posts or None,
            valuation_mark=FreshnessBuilder.valuation_from_env(),
        ).build()

    def system_prompt(self) -> str | None:
        """Full [CORE]+[CANONICALS]+[FRESHNESS] system prompt, or None if the
        guardrail files aren't present (falls back to the legacy system_base)."""
        if not self.assembler:
            return None
        return self.assembler.assemble(self.build_freshness())

    # ------------------------------------------------------------------ #
    async def handle(self, msg: IncomingMessage) -> AgentResponse | None:
        started = time.perf_counter()
        thread_key = f"{msg.channel}:{msg.chat_id}:{msg.thread_id or 'main'}"
        user_key = f"{msg.channel}:{msg.author.external_id}"

        # 1) Moderation (skip in private chats and for admins).
        if not msg.is_private:
            verdict = await self.moderator.evaluate(msg)
            if verdict.flagged:
                return await self._moderation_response(msg, verdict, thread_key, started)

        # 2) Working memory + long-term profile.
        self.memory.add_turn(thread_key, "user", msg.text)
        profile = await self.memory.get_profile(user_key, msg.author.display_name)
        profile.touch()

        # 3) Route.
        routing = await self.router.route(msg.text, msg.reply_to_text)
        profile.language = routing.language
        if routing.persona != "unknown":
            profile.persona = routing.persona
        if routing.technical_level != "unknown":
            profile.technical_level = routing.technical_level
        for t in routing.topics:
            profile.add_interest(t)
        if routing.is_question:
            profile.record_question(msg.text)

        # 4) Decide whether to speak (avoid group spam).
        if not self._should_engage(msg, routing):
            await self.memory.save_profile(profile)
            return None

        # 4b) Deterministic compliance pre-intercepts (seed phrase, prompt
        #     injection, price speculation) — these must not reach the LLM.
        intercept = self.rails.pre_intercept(msg.text)
        if intercept:
            response = AgentResponse(
                text=intercept.text[: self.max_reply],
                confidence=Confidence.HIGH,
                confidence_score=1.0,
                mode=Mode.MODERATOR if intercept.category == "security" else routing.mode,
                persona=profile.persona,
                language=routing.language,
                escalate=intercept.escalate,
                reply_to_message_id=msg.message_id,
                meta={"rail": intercept.category, "intercepted": True},
            )
            self.memory.add_turn(thread_key, "assistant", response.text)
            await self.memory.save_profile(profile)
            await self._log(msg, routing, [], response, user_key, started)
            return response

        # 4c) Kill switch — incident mode: static FAQ only, no LLM.
        if self.paused:
            response = self._static_response(msg, self._static_faq(), "paused")
            await self.memory.save_profile(profile)
            await self._log(msg, routing, [], response, user_key, started)
            return response

        # 4d) Rate limiting + global spend cap — cheap static reply, no LLM.
        if not msg.author.is_admin:
            decision = self.rate_limiter.check(user_key)
            if not decision.allowed:
                response = self._static_response(msg, decision.retry_hint, "rate_limited")
                response.meta["rate_status"] = decision.status.value
                await self.memory.save_profile(profile)
                await self._log(msg, routing, [], response, user_key, started)
                return response

        # 5) Retrieve.
        retrieved: list[RetrievedChunk] = []
        if routing.needs_docs:
            retrieved = await self.retriever.retrieve(msg.text)

        # 6) Synthesize + 7) confidence gate.
        response = await self._answer(msg, routing, retrieved, profile, thread_key)

        # 7b) Share-with-a-friend cadence: after every 4th genuinely helpful
        #     answer (confident, not gated/blocked/escalated, in a DM), flag a
        #     share nudge for the channel to render. Never on refusals.
        if (
            msg.is_private
            and response.should_reply
            and response.confidence != Confidence.LOW
            and not response.escalate
            and not response.meta.get("gated")
            and not response.meta.get("rails", {}).get("blocked")
        ):
            profile.helpful_answers += 1
            if profile.helpful_answers % 4 == 0:
                response.meta["share_nudge"] = True
            # Engagement: daily streak + XP for a substantive interaction.
            try:
                streak, new_day = self.xp.touch(user_key, msg.author.display_name or "")
                self.xp.award(user_key, 5, "helpful_answer", msg.author.display_name or "")
                if new_day and streak in (3, 7, 14, 30):
                    response.meta["streak_milestone"] = streak
            except Exception as exc:  # noqa: BLE001 - XP must never break a reply
                log.warning("xp.touch_failed", error=str(exc))

        # 8) Leads.
        await self._handle_leads(msg, routing, profile, response)

        # 9) Persist memory + log decision.
        if response.should_reply:
            self.memory.add_turn(thread_key, "assistant", response.text)
        await self.memory.save_profile(profile)
        await self._log(msg, routing, retrieved, response, user_key, started)
        return response

    # ------------------------------------------------------------------ #
    def _should_engage(self, msg: IncomingMessage, routing: Routing) -> bool:
        if msg.is_private:
            return True
        # In a group, always engage when directly addressed (@mention or reply).
        if msg.raw.get("addressed"):
            return True
        # Untagged: jump in on any question, or a clearly Stobox-relevant message
        # (the router tags topics / needs_docs for those) — but stay quiet on pure
        # chatter ("hey", "gm", "lol"), which carries no question, docs, or topics.
        return routing.is_question or routing.needs_docs or bool(routing.topics)

    async def _answer(
        self,
        msg: IncomingMessage,
        routing: Routing,
        retrieved: list[RetrievedChunk],
        profile: UserProfile,
        thread_key: str,
    ) -> AgentResponse:
        history = self._format_history(thread_key)
        context, citations = self._format_context(retrieved)
        user_context = self._user_summary(profile)
        # Prefer the assembled [CORE]+[CANONICALS]+[FRESHNESS] system prompt;
        # fall back to the legacy persona prompt if guardrail files are absent.
        system = self.system_prompt() or self.prompts.render(
            "system_base", persona=profile.persona, mode=routing.mode.value
        )

        if routing.mode == Mode.SALES_ASSISTANT and self.leads.enabled:
            user_prompt = self.prompts.render(
                "lead_qualification",
                user_summary=user_context,
                language=routing.language,
                question=msg.text,
                context=context or "(no documentation retrieved)",
            )
        elif not routing.needs_docs:
            # Greetings / small talk — don't force the strict doc-answer path.
            user_prompt = self.prompts.render(
                "community_reply",
                question=msg.text,
                history=history,
                language=routing.language,
                user_context=user_context,
            )
        else:
            detail = bool(msg.raw.get("detail"))
            detail_mode = (
                "DETAIL MODE ON: the user explicitly asked for the full version — go "
                "comprehensive and structured (up to ~1500 characters), cover the key "
                "angles, but stay grounded and cite sources."
                if detail else "detail_mode: off (keep it short)."
            )
            user_prompt = self.prompts.render(
                "answer_synthesis",
                question=msg.text,
                history=history,
                language=routing.language,
                context=context or "(no documentation retrieved)",
                user_context=user_context,
                detail_mode=detail_mode,
                bucket=profile.user_key,
            )

        # Short answers by default; the full version only on explicit request.
        detail = bool(msg.raw.get("detail"))
        reply_cap = 700 if (routing.needs_docs and not detail) else (
            1600 if detail else 220)
        result = await self.reasoner.complete(
            [ChatMessage("system", system), ChatMessage("user", user_prompt)],
            max_tokens=reply_cap,
        )
        self.rate_limiter.record_spend(result.output_tokens)
        clean, self_conf, used_sources = self.confidence.parse(result.text)
        # Canonicals are authoritative grounding: an answer the model marks as
        # canonicals-based must not be IDK-gated for lacking retrieved chunks.
        canonical_grounded = any("canonical" in s.lower() for s in used_sources)
        cited = bool(citations) or canonical_grounded
        score = self.confidence.score(retrieved, self_conf, cited)
        if canonical_grounded and self_conf is not None:
            score = max(score, round(min(1.0, 0.2 + 0.8 * self_conf), 3))

        response = AgentResponse(
            text=clean[: self.max_reply],
            confidence_score=score,
            confidence=self.confidence.label(score),
            citations=citations,
            mode=routing.mode,
            persona=profile.persona,
            language=routing.language,
            reply_to_message_id=msg.message_id,
            meta={
                "tokens_in": result.input_tokens,
                "tokens_out": result.output_tokens,
                "model": result.model,
                "provider": result.provider,
                "prompt_version": self.prompts.version_of("answer_synthesis", profile.user_key),
            },
        )

        # Anti-hallucination gate — and the unanswered-question loop: capture
        # the question, so admins can /answer it and the asker gets a follow-up.
        # Fires on low confidence OR when the model itself declares it doesn't
        # know (an "unclear answer" is an unanswered question too).
        model_idk = "don't know based on the current documentation" in clean.lower()
        if routing.needs_docs and (self.confidence.below_threshold(score) or model_idk):
            response.text = _IDK["en"]   # English-only communication policy
            response.confidence = Confidence.LOW
            response.citations = []
            response.escalate = True
            response.meta["gated"] = True
            try:
                entry, is_new = self.qa.capture(
                    msg.text, channel=msg.channel, chat_id=msg.chat_id,
                    message_id=msg.message_id,
                    user_key=f"{msg.channel}:{msg.author.external_id}",
                    language=routing.language,
                )
                response.meta["qa"] = {"qid": entry.qid, "new": is_new,
                                       "question": entry.question}
            except Exception as exc:  # noqa: BLE001 - capture must never break replies
                log.error("qa.capture_failed", error=str(exc))

        # Deterministic compliance post-processing (blocks forbidden claims,
        # appends disclaimer / anti-impersonation warning where required).
        rail = self.rails.post_process(response.text, msg.text)
        response.text = rail.text[: self.max_reply]
        if rail.blocked:
            response.citations = []
            response.escalate = True
        if rail.escalate:
            response.escalate = True
        response.meta["rails"] = {
            "disclaimer": rail.disclaimer_added,
            "impersonation": rail.impersonation_added,
            "blocked": rail.blocked,
            "violations": rail.violations,
        }
        # Cited, confident answers are worth spreading — channels may attach a
        # one-tap "share this answer" affordance.
        response.meta["shareable"] = bool(
            response.citations
            and response.confidence != Confidence.LOW
            and not response.meta.get("gated")
            and not rail.blocked
        )
        return response

    async def _handle_leads(
        self, msg: IncomingMessage, routing: Routing, profile: UserProfile, response: AgentResponse
    ) -> None:
        if not self.leads.enabled:
            return
        email = self.leads.extract_email(msg.text)
        if email:
            profile.email = email
        self.leads.update_score(
            profile, buying_intent=routing.buying_intent, has_email=bool(email)
        )
        if await self.leads.handoff(profile):
            response.lead_captured = True

    async def _moderation_response(
        self, msg: IncomingMessage, verdict, thread_key: str, started: float
    ) -> AgentResponse:
        response = AgentResponse(
            text=verdict.warn_text,
            mode=Mode.MODERATOR,
            moderation=verdict.action,
            escalate=verdict.alert_admin,
            reply_to_message_id=msg.message_id,
            meta={
                "category": verdict.category,
                "score": verdict.score,
                "reason": verdict.reason,
                "delete": verdict.delete,
                "mute_minutes": verdict.mute_minutes,
                "strike_count": verdict.strike_count,
                "dm_text": verdict.dm_text,
                "alert_admin": verdict.alert_admin,
                "offender_user_key": f"{msg.channel}:{msg.author.external_id}",
                "offender_id": msg.author.external_id,
                "offender_name": msg.author.display_name,
            },
        )
        await self._log(
            msg, Routing(mode=Mode.MODERATOR), [], response,
            f"{msg.channel}:{msg.author.external_id}", started, answered=False,
        )
        return response

    # ------------------------------------------------------------------ #
    def _format_history(self, thread_key: str) -> str:
        turns = self.memory.history(thread_key)[:-1]  # exclude the current user turn
        if not turns:
            return "(new conversation — this is their first message)"
        labels = {"user": "User", "assistant": "You"}
        return "\n".join(
            f"{labels.get(t.role, t.role)}: {t.text[:400]}" for t in turns[-8:]
        )

    def _format_context(self, retrieved: list[RetrievedChunk]) -> tuple[str, list[Citation]]:
        if not retrieved:
            return "", []
        blocks: list[str] = []
        citations: list[Citation] = []
        seen: set[str] = set()
        for rc in retrieved:
            meta = rc.chunk.meta
            title = meta.title if meta else "Stobox docs"
            label = title + (f" §{rc.chunk.section}" if rc.chunk.section else "")
            blocks.append(f"[{label}]\n{rc.chunk.text}")
            key = f"{title}|{rc.chunk.section}"
            if key not in seen:
                seen.add(key)
                citations.append(
                    Citation(
                        title=title,
                        section=rc.chunk.section,
                        version=meta.version if meta else None,
                        source_file=meta.source_file if meta else None,
                        source_url=meta.source_url if meta else None,
                    )
                )
        return "\n\n".join(blocks), citations

    @staticmethod
    def _user_summary(p: UserProfile) -> str:
        parts = [f"name: {p.display_name}" if p.display_name else "name: unknown"]
        if p.persona not in ("auto", "unknown"):
            parts.append(f"likely a {p.persona}")
        if p.technical_level != "unknown":
            parts.append(f"technical level: {p.technical_level}")
        if p.interests:
            parts.append(f"has asked about: {', '.join(p.interests[:5])}")
        if p.customer_stage not in ("member",):
            parts.append(f"journey stage: {p.customer_stage}")
        if len(p.recent_questions) > 1:
            parts.append(f"previous question: {p.recent_questions[-2][:100]}")
        return "; ".join(parts)

    async def _log(
        self, msg, routing, retrieved, response, user_key, started, answered=True
    ) -> None:
        await self.decisions.record(
            Decision(
                channel=msg.channel,
                chat_id=msg.chat_id,
                user_key=user_key,
                mode=routing.mode.value,
                persona=response.persona,
                language=response.language,
                question=msg.text,
                confidence=response.confidence.value,
                confidence_score=response.confidence_score,
                retrieved=len(retrieved),
                top_score=retrieved[0].score if retrieved else 0.0,
                sources=[c.title for c in response.citations],
                latency_ms=(time.perf_counter() - started) * 1000,
                moderation=response.moderation.value,
                escalated=response.escalate,
                lead_captured=response.lead_captured,
                answered=answered and response.should_reply,
                tokens_in=response.meta.get("tokens_in", 0),
                tokens_out=response.meta.get("tokens_out", 0),
                meta={"topics": routing.topics},
            )
        )
