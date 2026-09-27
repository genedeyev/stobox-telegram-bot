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

CANT_VERIFY = ("I can't verify that against the published record right now, so I won't guess. "
               "The official record is https://www.stobox.io/stbu and the team is at info@stobox.io.")
PAUSED = ("I've reached my limit for today and I'm pausing answers until tomorrow. "
          "The official record is https://www.stobox.io and the team is at info@stobox.io.")
REFUSED = ("I can't help with that one. For anything about Stobox, the official record is "
           "https://www.stobox.io and the team is at info@stobox.io.")


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
        sources = (f"[SITE] ({site.source}, fetched {stamp})\n{site.block()}\n\n"
                   f"[SIG] (Stobox Intelligence Graph; lower authority than [SITE])\n{sig_ctx}")
        usd, verdict, correction, tries = 0.0, "unchecked", None, 0
        text = ""
        for tries in (1, 2):  # noqa: B007 - read after the loop
            try:
                draft = await self.model.draft(sources, question, correction)
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
            if verdict == "contradicted":
                correction = fc.correction or "; ".join(i.get("truth", "") for i in fc.issues)
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
