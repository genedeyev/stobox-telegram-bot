"""Leads, kept from stage 0 (Gene, 27.09.2026): admin DM plus the CRM webhook.

An email counts only in a private chat, only the first one per user, never one
of our own mailboxes. One CRM post per (user, email), a daily cap, https only,
and the `x-mcp-secret` header the site's /api/mcp-lead intake expects.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .ledger import log

_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_OWN = ("stobox.io", "stoboxplatform.com")
_ISSUER = re.compile(
    r"(?i)\b(tokeni[sz]e|tokeni[sz]ation|issue|issuance|raise capital|offering|our company|"
    r"my company|our fund|my fund|real estate|intelligence|raisable|compass|pricing|demo|call)\b")


def extract_email(text: str) -> str | None:
    for m in _EMAIL.finditer(text or ""):
        dom = m.group(0).rsplit("@", 1)[-1].lower()
        if not any(dom == d or dom.endswith("." + d) for d in _OWN):
            return m.group(0)
    return None


class Leads:
    def __init__(self, state_dir: Path, webhook: str = "", secret: str = "",
                 daily_cap: int = 20) -> None:
        self.path = state_dir / "leads.json"
        self.webhook = webhook if webhook.startswith("https://") else ""
        if webhook and not self.webhook:
            log.error("lead.webhook_not_https")
        self.secret, self.cap = secret, daily_cap

    def _state(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"users": {}, "days": {}}

    def _save(self, st: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            tmp.replace(self.path)
        except OSError as exc:
            log.error("lead.state_write_failed", error=str(exc))

    async def consider(self, *, private: bool, user_id: str, name: str, text: str,
                       history: list[str]) -> str | None:
        """Returns an admin summary the first time a user becomes a lead, else None."""
        if not private:
            return None
        email = extract_email(text)
        if not email or not _ISSUER.search(" ".join([*history[-4:], text])):
            return None
        st = self._state()
        if user_id in st["users"]:
            return None
        st["users"][user_id] = {"email": email, "at": datetime.now(UTC).isoformat()}
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        summary = (f"New lead from Stoby (Telegram DM)\nName: {name}\nEmail: {email}\n"
                   f"Recent messages:\n" + "\n".join(f"- {h[:200]}" for h in [*history[-4:], text]))
        posted = False
        if self.webhook and st["days"].get(day, 0) < self.cap:
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.post(self.webhook, headers={"x-mcp-secret": self.secret},
                                     json={"email": email, "firstname": name, "message": summary,
                                           "source": "telegram-bot"})
                posted = r.status_code < 300
                if not posted:
                    log.error("lead.handoff_rejected", status=r.status_code)
            except httpx.HTTPError as exc:
                log.error("lead.handoff_failed", error=type(exc).__name__)
        if posted:
            st["days"][day] = st["days"].get(day, 0) + 1
        self._save(st)
        log.info("lead.handoff" if posted else "lead.captured_no_sink",
                 email_domain=email.rsplit("@", 1)[-1])
        return summary
