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
