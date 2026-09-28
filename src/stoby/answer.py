"""One question, start to finish.

rails before the model → spend cap → site → SIG context → draft → grounding and
SIG fact_check (one regeneration) → rails after the model → one `answer` line.
Any source down means a fixed reply and no model call: Stoby never answers from
the model's memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .ledger import SpendLedger, log
from .llm import Answerer, ModelDown
from .rails import DISCLAIMER, ComplianceRails, canned_buy_answer, is_buy_intent, no_em_dash
from .sources.sig import SigClient, SigDown
from .sources.site import SiteSource, SiteUnavailable
from .verify import ungrounded

CANT_VERIFY = ("🤔 I can't check that right now, so I won't guess. "
               "You'll find the facts on https://www.stobox.io/stbu, or ask the team at support@stobox.io 💬")
PAUSED = ("😴 I've answered a lot today and I'm taking a break until tomorrow. "
          "Meanwhile: https://www.stobox.io or support@stobox.io 💬")
REFUSED = ("🙏 I can't help with that one. For anything about Stobox: https://www.stobox.io "
           "or support@stobox.io 💬")


@dataclass
class Reply:
    text: str
    outcome: str                        # answered | fixed_* | rail
    usd: float = 0.0
    meta: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, site: SiteSource, sig: SigClient, model: Answerer,
                 ledger: SpendLedger, rails: ComplianceRails | None = None) -> None:
        self.site, self.sig, self.model, self.ledger = site, sig, model, ledger
        self.rails = rails or ComplianceRails()

    async def answer(self, question: str, *, chat: str = "", user: str = "") -> Reply:
        started = time.perf_counter()
        reply = await self._answer(question)
        reply.meta["latency_ms"] = round((time.perf_counter() - started) * 1000)
        log.info("answer", chat=chat, user=user, outcome=reply.outcome,
                 usd=round(reply.usd, 6), **reply.meta)
        return reply

    async def _answer(self, question: str) -> Reply:
        pre = self.rails.pre_intercept(question)
        if pre is not None:
            return Reply(no_em_dash(pre.text), "rail", meta={"category": pre.category})
        if not self.ledger.allows():
            return Reply(PAUSED, "fixed_cap")
        try:
            site = await self.site.facts()
        except SiteUnavailable as exc:
            return Reply(CANT_VERIFY, "fixed_site_down", meta={"error": str(exc)})
        if is_buy_intent(question):
            canned = canned_buy_answer(site.where_to_buy)
            if canned:
                return Reply(f"{canned}\n\n{DISCLAIMER}", "rail", meta={"category": "buy_intent"})
        try:
            sig_ctx = await self.sig.context(question)
        except SigDown as exc:
            return Reply(CANT_VERIFY, "fixed_sig_down", meta={"error": str(exc)})

        stamp = time.strftime("%d %B %Y %H:%M UTC", time.gmtime(site.fetched_at))
        site_block = f"[SITE] ({site.source}, fetched {stamp})\n{site.block()}"
        sig_block = f"[SIG] (Stobox Intelligence Graph; lower authority than [SITE])\n{sig_ctx}"
        sources = f"{site_block}\n\n{sig_block}"
        usd, verdict, correction, tries = 0.0, "unchecked", None, 0
        text = ""
        for tries in (1, 2):  # noqa: B007 - read after the loop
            try:
                draft = await self.model.draft(sig_block, question, correction,
                                               site_block=site_block)
            except ModelDown as exc:
                return Reply(CANT_VERIFY, "fixed_model_down", usd, {"error": str(exc)})
            usd += draft.usd
            self.ledger.add(draft.usd)
            if draft.stop_reason == "refusal" or not draft.text:
                return Reply(REFUSED, "fixed_refusal", usd)
            missing = ungrounded(draft.text, sources)
            if missing:
                verdict, correction = "ungrounded", (
                    f"these figures or addresses are not in the sources: {missing[:8]}; "
                    "remove them or use the exact source wording")
                continue
            try:
                fc = await self.sig.fact_check(draft.text)
            except SigDown as exc:
                return Reply(CANT_VERIFY, "fixed_sig_down", usd, {"error": str(exc)})
            verdict = fc.verdict
            # Only SIG's deterministic canon rules (named issues) can reject a
            # draft. A contradiction from SIG's model alone, with no rule
            # behind it, does not outrank the site: 28.09.2026 it rejected the
            # site's own sentence "STBX is tokenized Class-C equity in Stobox
            # Technologies Inc." (equity in Technologies, issued by Tokenized
            # Equities Ltd: both true). It is logged for the canon, not obeyed.
            if verdict == "contradicted" and not fc.issues:
                log.warning("sig.model_disagrees", correction=(fc.correction or "")[:200],
                            draft=draft.text[:200])
                verdict = "sig_model_disputed"
            elif verdict == "contradicted":
                correction = fc.correction or "; ".join(i.get("truth", "") for i in fc.issues)
                log.info("sig.rule_rejected", rules=[i.get("rule") for i in fc.issues],
                         draft=draft.text[:200])
                continue
            text = draft.text
            break
        if not text:
            return Reply(CANT_VERIFY, "fixed_unverified", usd,
                         {"verdict": verdict, "tries": tries})
        post = self.rails.post_process(text, question, trusted=site.addresses,
                                       where_to_buy=site.where_to_buy)
        return Reply(post.text, "answered", usd,
                     {"verdict": verdict, "tries": tries, "site_age_h": round(site.age_hours(), 2),
                      "blocked": post.blocked})
