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
    Citation,
    Confidence,
    IncomingMessage,
    Mode,
    ModerationAction,
)

log = get_logger(__name__)

_IDK = {
    "en": "I don't know based on the current documentation. Let me connect you with the Stobox team — you can also reach support via /support.",
    "ru": "Я не могу ответить на основе текущей документации. Свяжу вас с командой Stobox — также доступна поддержка через /support.",
    "uk": "Я не можу відповісти на основі поточної документації. З'єднаю вас із командою Stobox — також доступна підтримка через /support.",
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
        self.leads = leads
        self.decisions = decision_log
        self.prompts = prompts
        self.indexer = indexer
        self.confidence = ConfidenceEngine(
            threshold=float(config.get("confidence.threshold", 0.55)),
            require_citations=bool(config.get("confidence.require_citations", True)),
        )
        self.max_reply = int(config.get("limits.max_reply_chars", 3500))
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
        moderator = Moderator(config, classifier)
        leads = LeadQualifier(config)
        decision_log = await build_decision_log(config)
        engine = cls(
            config, reasoner, classifier, retriever, memory, moderator,
            leads, decision_log, get_prompts(), indexer,
        )
        engine.last_sync = datetime.now(UTC)
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

    async def sync_knowledge(self) -> dict[str, int]:
        """Crawl stobox.io + ingest the GitHub repos into the live index."""
        from ..knowledge.sync import sync_sources

        results = await sync_sources(self.indexer, self.config)
        self.last_sync = datetime.now(UTC)
        return results

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

        # 5) Retrieve.
        retrieved: list[RetrievedChunk] = []
        if routing.needs_docs:
            retrieved = await self.retriever.retrieve(msg.text)

        # 6) Synthesize + 7) confidence gate.
        response = await self._answer(msg, routing, retrieved, profile, thread_key)

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
        if msg.author.is_admin and msg.raw.get("addressed"):
            return True
        # In groups, engage when directly addressed or when it's a real question.
        if msg.raw.get("addressed"):
            return True
        return routing.is_question and routing.needs_docs

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
        # Prefer the assembled [CORE]+[CANONICALS]+[FRESHNESS] system prompt;
        # fall back to the legacy persona prompt if guardrail files are absent.
        system = self.system_prompt() or self.prompts.render(
            "system_base", persona=profile.persona, mode=routing.mode.value
        )

        if routing.mode == Mode.SALES_ASSISTANT and self.leads.enabled:
            user_prompt = self.prompts.render(
                "lead_qualification",
                user_summary=self._user_summary(profile),
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
            )
        else:
            user_prompt = self.prompts.render(
                "answer_synthesis",
                question=msg.text,
                history=history,
                language=routing.language,
                context=context or "(no documentation retrieved)",
                bucket=profile.user_key,
            )

        result = await self.reasoner.complete(
            [ChatMessage("system", system), ChatMessage("user", user_prompt)]
        )
        clean, self_conf, _used = self.confidence.parse(result.text)
        cited = bool(citations)
        score = self.confidence.score(retrieved, self_conf, cited)

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

        # Anti-hallucination gate.
        if routing.needs_docs and self.confidence.below_threshold(score):
            response.text = _IDK.get(routing.language, _IDK["en"])
            response.confidence = Confidence.LOW
            response.citations = []
            response.escalate = True
            response.meta["gated"] = True

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
        warn_text = ""
        if verdict.action == ModerationAction.WARN:
            warn_text = (
                "⚠️ Please keep the discussion constructive and on-topic. "
                "Repeated violations may lead to a mute."
            )
        response = AgentResponse(
            text=warn_text,
            mode=Mode.MODERATOR,
            moderation=verdict.action,
            escalate=verdict.category in ("scam", "phishing"),
            reply_to_message_id=msg.message_id,
            meta={"category": verdict.category, "score": verdict.score, "reason": verdict.reason},
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
            return "(new conversation)"
        return "\n".join(f"{t.role}: {t.text[:300]}" for t in turns[-6:])

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
        return (
            f"persona={p.persona}, stage={p.customer_stage}, level={p.technical_level}, "
            f"interests={', '.join(p.interests[:5]) or 'n/a'}, "
            f"products={', '.join(p.products_discussed[:5]) or 'n/a'}, lead_score={p.lead_score}"
        )

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
