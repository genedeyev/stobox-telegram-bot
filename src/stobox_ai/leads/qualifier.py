"""Lead scoring + MQL handoff.

Buying intent (from the intent router) bumps a per-user lead score. When an email
appears and intent is present, the lead is an MQL – and until the Twenty CRM is
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
# Our own mailboxes show up in Stoby's answers and get quoted back ("I wrote to
# support@stobox.io"). They are never a lead.
_OWN_DOMAINS = ("stobox.io", "stoboxplatform.com")


def _is_own(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower().rstrip(".")
    return any(domain == d or domain.endswith("." + d) for d in _OWN_DOMAINS)


class LeadQualifier:
    def __init__(self, config: Config) -> None:
        leads = config.section("leads")
        self.enabled = bool(leads.get("enabled", True))
        # Arevik: stop the MQL email spam. Off by default – leads still flow to
        # the CRM webhook and a one-time admin DM, just no emails to the inbox.
        self.email_mql = bool(leads.get("email_mql", False))
        self.mql_inbox = leads.get("mql_inbox") or "info@stobox.io"
        self.webhook = leads.get("crm_webhook") or None
        # The site's /api/mcp-lead intake authenticates machine callers with a
        # shared secret header (same value as MCP_LEAD_SECRET on the site).
        import os

        self.webhook_secret = os.environ.get("CRM_WEBHOOK_SECRET") or None
        if self.webhook and not str(self.webhook).startswith("https://"):
            log.error("lead.webhook_not_https", hint="CRM_WEBHOOK_URL must be https://")
            self.webhook = None
        # Global bound on CRM posts per UTC day: one chat account must not be
        # able to flood the CRM, whatever the per-user dedupe misses.
        self.daily_cap = int(leads.get("crm_daily_cap", 20))
        self._posted_day: str = ""
        self._posted_today = 0
        self.source = leads.get("crm_source", "telegram-bot")
        self.email = EmailSender()

    @staticmethod
    def extract_email(text: str) -> str | None:
        for m in _EMAIL.finditer(text or ""):
            if not _is_own(m.group(0)):
                return m.group(0)
        return None

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
            # Fields the site's /api/mcp-lead intake maps onto the Twenty card.
            "firstname": profile.display_name or "",
            "message": self.summary(profile),
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
            f"Name:          {profile.display_name or ' – '}",
            f"Email:         {profile.email or ' – '}",
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
                     "(stobox.io/contact), or readiness score (stobox.io/readiness).")
        return "\n".join(lines)

    async def handoff(self, profile: UserProfile) -> bool:
        """Deliver a qualified MQL. Emails the team inbox (and POSTs the CRM
        webhook if configured). Returns True once the lead is qualified, even if
        no delivery channel is set up yet (so callers can mark it captured)."""
        if not (self.enabled and profile.email and profile.lead_score >= 40):
            return False
        delivered = False

        # 1) Email the MQL summary to the team inbox – OFF by default (Arevik).
        if self.email_mql and self.email.configured and self.mql_inbox:
            subject = (f"[MQL] {profile.display_name or profile.email} – "
                       f"score {profile.lead_score}")
            ok = await asyncio.to_thread(
                self.email.send, self.mql_inbox, subject, self.summary(profile)
            )
            delivered = delivered or ok

        # 2) Optional CRM webhook – set CRM_WEBHOOK_URL when Twenty is connected.
        #    Once per (user, email), and never past the daily cap.
        from datetime import UTC, datetime

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._posted_day:
            self._posted_day, self._posted_today = today, 0
        already = profile.email in profile.crm_posted
        capped = self._posted_today >= self.daily_cap
        if self.webhook and already:
            return True                      # this email is already in the CRM
        if self.webhook and capped:
            log.warning("lead.daily_cap_reached", cap=self.daily_cap)
        elif self.webhook:
            try:
                import httpx

                headers = {"x-mcp-secret": self.webhook_secret} if self.webhook_secret else {}
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(self.webhook, json=self._payload(profile),
                                          headers=headers)
                if r.status_code < 300:
                    delivered = True
                    self._posted_today += 1
                    profile.crm_posted.append(profile.email)
                else:
                    log.error("lead.handoff_rejected", status=r.status_code)
            except Exception as exc:  # noqa: BLE001
                log.error("lead.handoff_failed", error=str(exc))

        if delivered:
            log.info("lead.handoff", email_domain=profile.email.rsplit("@", 1)[-1],
                     score=profile.lead_score,
                     inbox=self.mql_inbox if self.email.configured else None)
        else:
            log.info("lead.captured_no_sink", email_domain=profile.email.rsplit("@", 1)[-1],
                     score=profile.lead_score,
                     hint="set SMTP_* to email the MQL inbox, or CRM_WEBHOOK_URL")
        return True
