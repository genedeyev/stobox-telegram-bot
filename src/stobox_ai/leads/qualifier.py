"""Lead scoring + MQL handoff.

Buying intent (from the intent router) bumps a per-user lead score. When an email
appears and intent is present, the lead is an MQL — and until the Twenty CRM is
connected, we email a plain-text summary of it to the team inbox (info@stobox.io
by default). No PII is placed in URLs.

When a CRM webhook is later configured (CRM_WEBHOOK_URL), the same MQL is also
POSTed there as JSON, so flipping to the CRM is a one-line env change.
"""

from __future__ import annotations

import asyncio
import re

from ..config import Config
from ..logging import get_logger
from ..memory.models import UserProfile
from ..ops.email import EmailSender

log = get_logger(__name__)
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


class LeadQualifier:
    def __init__(self, config: Config) -> None:
        leads = config.section("leads")
        self.enabled = bool(leads.get("enabled", True))
        self.mql_inbox = leads.get("mql_inbox") or "info@stobox.io"
        self.webhook = leads.get("crm_webhook") or None
        self.source = leads.get("crm_source", "telegram-bot")
        self.email = EmailSender()

    @staticmethod
    def extract_email(text: str) -> str | None:
        m = _EMAIL.search(text)
        return m.group(0) if m else None

    def update_score(self, profile: UserProfile, *, buying_intent: bool, has_email: bool) -> None:
        if buying_intent:
            profile.lead_score = min(100, profile.lead_score + 20)
            if profile.customer_stage in ("member", "curious"):
                profile.customer_stage = "evaluating"
        if has_email:
            profile.lead_score = min(100, profile.lead_score + 40)
            profile.customer_stage = "lead"

    def _payload(self, profile: UserProfile) -> dict:
        return {
            "source": self.source,
            "email": profile.email,
            "name": profile.display_name,
            "lead_score": profile.lead_score,
            "stage": profile.customer_stage,
            "interests": profile.interests,
            "products_discussed": profile.products_discussed,
            "language": profile.language,
            "notes": profile.notes,
            "recent_questions": profile.recent_questions[-5:],
        }

    def summary(self, profile: UserProfile) -> str:
        """Human-readable MQL summary for the team inbox."""
        lines = [
            "New MQL from the Stobox Telegram community (via Stoby).",
            "",
            f"Name:          {profile.display_name or '—'}",
            f"Email:         {profile.email or '—'}",
            f"Lead score:    {profile.lead_score}/100",
            f"Stage:         {profile.customer_stage}",
            f"Language:      {profile.language}",
        ]
        if profile.products_discussed:
            lines.append(f"Asset/product: {', '.join(profile.products_discussed)}")
        if profile.interests:
            lines.append(f"Interests:     {', '.join(profile.interests[-8:])}")
        if profile.notes.strip():
            lines.append(f"Notes:         {profile.notes.strip()}")
        recent = profile.recent_questions[-5:]
        if recent:
            lines.append("")
            lines.append("Recent questions:")
            lines += [f"  • {q}" for q in recent]
        lines.append("")
        lines.append("Suggested next touch: product (app.stobox.io), contact form "
                     "(stobox.io/contact), or Readiness Score (stobox.io/compass).")
        return "\n".join(lines)

    async def handoff(self, profile: UserProfile) -> bool:
        """Deliver a qualified MQL. Emails the team inbox (and POSTs the CRM
        webhook if configured). Returns True once the lead is qualified, even if
        no delivery channel is set up yet (so callers can mark it captured)."""
        if not (self.enabled and profile.email and profile.lead_score >= 40):
            return False
        delivered = False

        # 1) Email the MQL summary to the team inbox (until the CRM is connected).
        if self.email.configured and self.mql_inbox:
            subject = (f"[MQL] {profile.display_name or profile.email} — "
                       f"score {profile.lead_score}")
            ok = await asyncio.to_thread(
                self.email.send, self.mql_inbox, subject, self.summary(profile)
            )
            delivered = delivered or ok

        # 2) Optional CRM webhook — set CRM_WEBHOOK_URL when Twenty is connected.
        if self.webhook:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(self.webhook, json=self._payload(profile))
                delivered = True
            except Exception as exc:  # noqa: BLE001
                log.error("lead.handoff_failed", error=str(exc))

        if delivered:
            log.info("lead.handoff", email=profile.email, score=profile.lead_score,
                     inbox=self.mql_inbox if self.email.configured else None)
        else:
            log.info("lead.captured_no_sink", email=profile.email,
                     score=profile.lead_score,
                     hint="set SMTP_* to email the MQL inbox, or CRM_WEBHOOK_URL")
        return True
