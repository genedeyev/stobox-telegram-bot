"""Email sender (Resend API preferred, SMTP fallback).

Used for the /email write-up and the MQL summary to the team inbox. Two ways to
configure, checked in this order:

  1. Resend  — set RESEND_API_KEY (+ EMAIL_FROM on a Resend-verified domain).
               Simplest; just an API key, no SMTP server.
  2. SMTP    — SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / EMAIL_FROM.

If neither is set, sending is disabled and callers degrade gracefully (the lead
is still captured; the user is told the team will follow up). Never blocks chat.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from ..logging import get_logger

log = get_logger(__name__)

# Resend's shared test sender — works without domain verification but can only
# deliver to the Resend account owner's own address. Fine for first smoke tests.
_RESEND_TEST_FROM = "Stoby <onboarding@resend.dev>"


class EmailSender:
    def __init__(self) -> None:
        self.host = os.environ.get("SMTP_HOST", "")
        self.port = int(os.environ.get("SMTP_PORT", "587"))
        self.user = os.environ.get("SMTP_USER", "")
        self.password = os.environ.get("SMTP_PASS", "")
        self.resend_key = os.environ.get("RESEND_API_KEY", "")
        default_from = _RESEND_TEST_FROM if self.resend_key else self.user
        self.sender = os.environ.get("EMAIL_FROM", default_from)

    @property
    def configured(self) -> bool:
        return bool(self.sender and (self.resend_key or self.host))

    @property
    def transport(self) -> str:
        return "resend" if self.resend_key else ("smtp" if self.host else "none")

    def send(self, to: str, subject: str, body_text: str) -> bool:
        """Blocking send — call via asyncio.to_thread. Returns success.

        Prefers Resend when RESEND_API_KEY is set, else SMTP.
        """
        if not self.configured:
            return False
        if self.resend_key:
            return self._send_resend(to, subject, body_text)
        return self._send_smtp(to, subject, body_text)

    def _send_resend(self, to: str, subject: str, body_text: str) -> bool:
        try:
            import httpx

            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.resend_key}",
                         "Content-Type": "application/json"},
                json={"from": self.sender, "to": [to],
                      "subject": subject, "text": body_text},
                timeout=20,
            )
            if resp.status_code >= 400:
                log.error("email.resend_failed", status=resp.status_code,
                          body=resp.text[:300])
                return False
            log.info("email.sent", to=to, subject=subject[:60], via="resend")
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("email.send_failed", error=str(exc), via="resend")
            return False

    def _send_smtp(self, to: str, subject: str, body_text: str) -> bool:
        try:
            msg = EmailMessage()
            msg["From"] = self.sender
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body_text)
            ctx = ssl.create_default_context()
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=20) as s:
                    if self.user:
                        s.login(self.user, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=20) as s:
                    s.starttls(context=ctx)
                    if self.user:
                        s.login(self.user, self.password)
                    s.send_message(msg)
            log.info("email.sent", to=to, subject=subject[:60], via="smtp")
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("email.send_failed", error=str(exc), via="smtp")
            return False


_EMAIL_RE = None


def valid_email(addr: str) -> bool:
    global _EMAIL_RE
    if _EMAIL_RE is None:
        import re
        _EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
    return bool(_EMAIL_RE.match(addr.strip()))
