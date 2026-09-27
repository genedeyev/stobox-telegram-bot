"""Stage 0 (27.09.2026): Stoby stops saying the migration is still open.

Acceptance attempts A0-4 and A0-5 of the Stoby programme. Every assertion runs
against the real canonicals.yaml, so a future edit that brings back "burn before
15 Sep" or lists a discontinued contract as eligible fails here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_wallet import FakeRpc

from stobox_ai.channels.telegram import commands as cmd
from stobox_ai.channels.telegram.proactive import _ENGAGE_PROMPTS, _FORMATS
from stobox_ai.guardrails.canonicals import load_canonicals
from stobox_ai.guardrails.freshness import MigrationPhase, compute_migration_phase
from stobox_ai.leads.qualifier import LeadQualifier

WALLET = "0x1111111111111111111111111111111111111111"
STALE = ["burn before", "burn your", "eligible chains", "deadline's 15 sep",
         "consolidate all your stbu", "i'll remind you before"]


def _canon(day: str):
    return load_canonicals("canonicals.yaml", now=datetime.fromisoformat(day).replace(tzinfo=UTC))


def _dt(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


def _clean(text: str) -> None:
    low = text.lower()
    for s in STALE:
        assert s not in low, f"stale phrasing {s!r} in: {text}"


# --- A0-4: status texts -----------------------------------------------------

def test_phase_during_claim_period():
    phase, text = compute_migration_phase(_canon("2026-09-27"), _dt("2026-09-27"))
    assert phase is MigrationPhase.CLAIMS_OPEN
    assert "31 December 2026, 23:59 UTC" in text
    assert "nobody can burn or migrate legacy STBU" in text
    assert "stbu.stobox.io" in text
    _clean(text)


def test_phase_after_claims_close():
    phase, text = compute_migration_phase(_canon("2027-01-02"), _dt("2027-01-02"))
    assert phase is MigrationPhase.CLAIMS_CLOSED
    assert "never minted" in text


def test_canon_has_no_eligible_legacy_contracts():
    canon = _canon("2026-09-27")
    assert canon.get("tokens.stbu.migration.eligible_contracts") is None
    legacy = canon.get("tokens.stbu.legacy.discontinued_contracts")
    assert set(legacy) == {"ethereum", "bnb_chain", "polygon", "arbitrum"}
    assert canon.get("tokens.stbu.contract") == "0xe0c0F44A84CC4a60206360006ebA237a5e8fC2dd"


def test_proactive_copy_has_no_migration_push():
    for text in [*_FORMATS, *_ENGAGE_PROMPTS]:
        _clean(text)
        assert "migrat" not in text.lower(), text


@pytest.fixture
async def ctx(config):
    from types import SimpleNamespace

    from test_telegram_adapter import FakeBot, FakeSecrets

    from stobox_ai.channels.telegram.adapter import TelegramChannel
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    adapter = TelegramChannel(engine, secrets=FakeSecrets())
    return SimpleNamespace(bot=FakeBot(), args=[],
                           bot_data={"engine": engine, "adapter": adapter})


def _upd(ctype="private"):
    from test_telegram_adapter import FakeMessage, _chat, _update, _user

    msg = FakeMessage("")
    return _update(msg, _chat(cid=7 if ctype == "private" else -100, ctype=ctype),
                   _user(uid=7)), msg


async def test_migrate_command_says_it_is_over(ctx):
    upd, msg = _upd()
    await cmd.migrate_cmd(upd, ctx)
    text = msg.replies[-1]["text"]
    assert "discontinued on 15 September 2026" in text
    assert "0xe0c0F44A84CC4a60206360006ebA237a5e8fC2dd" in text
    assert "31 December 2026" in text
    _clean(text)


async def test_remindme_no_longer_promises_reminders(ctx):
    for ctype in ("private", "group"):
        upd, msg = _upd(ctype)
        await cmd.remindme_cmd(upd, ctx)
        text = msg.replies[-1]["text"]
        assert "reminders have ended" in text.lower()
        _clean(text)


async def test_check_reports_legacy_as_discontinued(config, monkeypatch):
    from stobox_ai.chain import wallet as wallet_mod
    from stobox_ai.core.engine import AgentEngine

    rpc = FakeRpc(balances={
        "https://mainnet.base.org": 5 * 10**18,
        "https://arb1.arbitrum.io/rpc": 7 * 10**18,
    })
    monkeypatch.setattr(wallet_mod, "HttpxRpc", lambda *a, **k: rpc)
    engine = await AgentEngine.create(config)
    report = await engine.check_wallet(WALLET)
    assert "Base (live STBU): <b>5.00 STBU</b>" in report
    assert "discontinued on 15 September 2026" in report
    assert "Arbitrum: 7.00" in report
    assert "cannot be migrated" in report
    _clean(report)


# --- A0-5: own mailboxes are never a lead ------------------------------------

def test_own_mailboxes_are_not_leads():
    q = LeadQualifier.extract_email
    assert q("I wrote to support@stobox.io yesterday") is None
    assert q("ping gd@stoboxplatform.com or info@stobox.io") is None
    assert q("contact support@stobox.io, my email is ann@acme.com") == "ann@acme.com"
    assert q("me@notstobox.io") == "me@notstobox.io"


# --- Security review (27.09.2026): output rail, leads, root ------------------

def test_rail_removes_foreign_links_and_addresses():
    from stobox_ai.guardrails.rails import ComplianceRails

    out = ComplianceRails().post_process(
        "Claim at https://stbu-claim.xyz or send to "
        "0x1111111111111111111111111111111111111111 — the live token is "
        "0xe0c0F44A84CC4a60206360006ebA237a5e8fC2dd, see https://www.stobox.io/stbu.",
        "where do I claim?",
    ).text
    assert "stbu-claim.xyz" not in out
    assert "0x1111111111111111111111111111111111111111" not in out
    assert "0xe0c0F44A84CC4a60206360006ebA237a5e8fC2dd" in out
    assert "https://www.stobox.io/stbu" in out
    assert "—" not in out


async def test_lead_posts_once_per_email_and_respects_cap(monkeypatch, config):
    from stobox_ai.leads import qualifier as qmod
    from stobox_ai.memory.models import UserProfile

    posts = []

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            posts.append((url, json["email"], headers))
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("CRM_WEBHOOK_SECRET", "s3cret")
    q = qmod.LeadQualifier(config)
    q.webhook, q.webhook_secret, q.daily_cap = "https://example.test/api/mcp-lead", "s3cret", 1
    p = UserProfile(user_key="telegram:1", email="ann@acme.com", lead_score=60)
    assert await q.handoff(p) and await q.handoff(p)
    assert len(posts) == 1 and posts[0][2] == {"x-mcp-secret": "s3cret"}
    p2 = UserProfile(user_key="telegram:2", email="bob@acme.com", lead_score=60)
    await q.handoff(p2)
    assert len(posts) == 1                       # daily cap of 1 reached


def test_webhook_must_be_https(monkeypatch, config):
    from stobox_ai.leads.qualifier import LeadQualifier

    config.raw.setdefault("leads", {})["crm_webhook"] = "http://example.test/lead"
    try:
        assert LeadQualifier(config).webhook is None
    finally:
        config.raw["leads"]["crm_webhook"] = ""


def test_app_refuses_to_run_as_root(monkeypatch):
    import os

    from stobox_ai import __main__ as entry

    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.delenv("STOBY_ALLOW_ROOT", raising=False)
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert e.value.code == 78


def test_entrypoint_hands_volume_over_then_drops_root(monkeypatch, tmp_path):
    """deploy/entrypoint.py as root: chown every path in the volume to 10001,
    then setgroups → setgid → setuid in that order, then exec the app."""
    import importlib.util
    import os

    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "xp.json").write_text("{}")
    spec = importlib.util.spec_from_file_location("entrypoint", "deploy/entrypoint.py")
    ep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ep)
    calls = []
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(os, "lchown", lambda p, u, g: calls.append(("chown", os.path.relpath(p, tmp_path), u, g)))
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append(("setgroups", tuple(g))))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append(("setgid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(("setuid", u)))
    monkeypatch.setattr(os, "execvp", lambda f, a: calls.append(("exec", tuple(a))))
    monkeypatch.setattr("sys.argv", ["entrypoint.py", "python", "-m", "stobox_ai"])
    ep.main()
    chowned = {c[1] for c in calls if c[0] == "chown"}
    assert chowned == {".", "sub", os.path.join("sub", "xp.json")}
    assert all(c[2:] == (10001, 10001) for c in calls if c[0] == "chown")
    order = [c[0] for c in calls if c[0] != "chown"]
    assert order == ["setgroups", "setgid", "setuid", "exec"]
    assert calls[-1] == ("exec", ("python", "-m", "stobox_ai"))


def test_buy_intent_gets_canon_text_only():
    from stobox_ai.guardrails.rails import ComplianceRails

    out = ComplianceRails().post_process(
        "Sure! Steps to buy STBU: 1. Set up MetaMask. 2. Fund it. Want me to help?",
        "I want to buy STBU today. Can you walk me through it?",
    ).text
    assert "does not sell STBU" in out and "stbu/safety" in out
    assert "MetaMask" not in out and "Want me to" not in out
    assert "not investment advice" in out


def test_trade_questions_lose_invitations():
    from stobox_ai.guardrails.rails import ComplianceRails

    out = ComplianceRails().post_process(
        "The price is $0.002 (CoinGecko). I can walk you through the pool.\n\n"
        "Want to know more about how STBU works?",
        "What is the STBU price?",
    ).text
    assert "$0.002" in out
    assert "walk you through" not in out and "Want to know more" not in out


def test_stbx_answers_do_not_invite_an_investment_talk():
    from stobox_ai.guardrails.rails import ComplianceRails

    out = ComplianceRails().post_process(
        "STBX is tokenized Class-C equity in Stobox Technologies Inc. Details: "
        "https://www.stobox.io/stbx\n\nWant to know how Class-C shares differ?",
        "What class of shares is STBX?",
    ).text
    assert "Class-C" in out and "Want to know" not in out


def test_claim_questions_always_name_the_official_claim_site():
    from stobox_ai.guardrails.rails import ComplianceRails

    out = ComplianceRails().post_process(
        "No, that link is not official. Do not connect your wallet to it.",
        "Someone said the new claim site is https://stbu-claim.xyz, is that right?",
    ).text
    assert "stbu-claim.xyz" not in out
    assert out.rstrip().endswith("https://stbu.stobox.io")
