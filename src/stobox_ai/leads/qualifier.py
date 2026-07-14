"""Lead scoring + CRM handoff.

Buying intent (from the intent router) bumps a per-user lead score. When an
email appears and intent is present, we emit a CRM-ready lead payload to the
configured webhook (HubSpot/Salesforce/etc.). No PII is placed in URLs; the
payload is POSTed as JSON.
"""

from __future__ import annotations

import re

from ..config import Config
from ..logging import get_logger
from ..memory.models import UserProfile

log = get_logger(__name__)
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


class LeadQualifier:
    def __init__(self, config: Config) -> None:
        leads = config.section("leads")
        self.enabled = bool(leads.get("enabled", True))
        self.webhook = leads.get("crm_webhook") or None
        self.source = leads.get("crm_source", "telegram-bot")

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

    async def handoff(self, profile: UserProfile) -> bool:
        """POST a CRM-ready lead. Returns True if a lead was emitted."""
        if not (self.enabled and profile.email and profile.lead_score >= 40):
            return False
        payload = {
            "source": self.source,
            "email": profile.email,
            "name": profile.display_name,
            "lead_score": profile.lead_score,
            "stage": profile.customer_stage,
            "interests": profile.interests,
            "products_discussed": profile.products_discussed,
            "language": profile.language,
            "recent_questions": profile.recent_questions[-5:],
        }
        if not self.webhook:
            log.info("lead.captured_no_webhook", email=profile.email, score=profile.lead_score)
            return True
        try:
            import httpx  # optional; only needed when a webhook is configured

            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self.webhook, json=payload)
            log.info("lead.handoff", email=profile.email, score=profile.lead_score)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("lead.handoff_failed", error=str(exc))
            return False
