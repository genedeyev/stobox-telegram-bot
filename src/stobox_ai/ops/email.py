"""Email follow-up sender.

Sends a detailed guide to a user who asked for it. SMTP is configured via env
(SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / EMAIL_FROM); if unset, sending
is disabled and the caller degrades to a CRM lead handoff so the team follows up
manually. Never blocks the chat flow.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from ..logging import get_logger

log = get_logger(__name__)


class EmailSender:
    def __init__(self) -> None:
        self.host = os.environ.get("SMTP_HOST", "")
        self.port = int(os.environ.get("SMTP_PORT", "587"))
        self.user = os.environ.get("SMTP_USER", "")
        self.password = os.environ.get("SMTP_PASS", "")
        self.sender = os.environ.get("EMAIL_FROM", self.user)

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    def send(self, to: str, subject: str, body_text: str) -> bool:
        """Blocking SMTP send — call via asyncio.to_thread. Returns success."""
        if not self.configured:
            return False
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
            log.info("email.sent", to=to, subject=subject[:60])
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("email.send_failed", error=str(exc))
            return False


_EMAIL_RE = None


def valid_email(addr: str) -> bool:
    global _EMAIL_RE
    if _EMAIL_RE is None:
        import re
        _EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
    return bool(_EMAIL_RE.match(addr.strip()))
