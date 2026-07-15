"""EmailSender transport selection: Resend preferred, SMTP fallback (offline)."""

from __future__ import annotations

import httpx

from stobox_ai.ops import email as email_mod
from stobox_ai.ops.email import EmailSender


def test_unconfigured_is_disabled(monkeypatch):
    for var in ("RESEND_API_KEY", "SMTP_HOST", "EMAIL_FROM", "SMTP_USER"):
        monkeypatch.delenv(var, raising=False)
    s = EmailSender()
    assert s.configured is False and s.transport == "none"
    assert s.send("x@y.com", "hi", "body") is False


def test_resend_preferred_when_key_set(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test123")
    monkeypatch.setenv("EMAIL_FROM", "Stoby <stoby@stobox.io>")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")   # present, but Resend wins
    s = EmailSender()
    assert s.configured is True and s.transport == "resend"

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return httpx.Response(200, json={"id": "abc"})

    monkeypatch.setattr(httpx, "post", fake_post)
    ok = s.send("lead@acme.com", "[MQL] Gene", "summary body")
    assert ok is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_test123"
    assert captured["json"]["to"] == ["lead@acme.com"]
    assert captured["json"]["from"] == "Stoby <stoby@stobox.io>"


def test_resend_default_test_sender(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test123")
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    s = EmailSender()
    assert s.sender == email_mod._RESEND_TEST_FROM   # onboarding@resend.dev


def test_resend_non_2xx_returns_false(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_bad")
    monkeypatch.setenv("EMAIL_FROM", "stoby@stobox.io")
    s = EmailSender()

    def fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(422, json={"message": "domain not verified"})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert s.send("x@y.com", "hi", "body") is False


def test_smtp_when_no_resend_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_FROM", "stoby@stobox.io")
    s = EmailSender()
    assert s.configured is True and s.transport == "smtp"
