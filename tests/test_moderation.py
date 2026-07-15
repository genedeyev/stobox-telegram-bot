"""Moderation stack tests: strikes ledger, severity policy, deterministic
detectors, impersonation, progressive discipline (offline)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stobox_ai.core.types import Author, ChatType, IncomingMessage, ModerationAction
from stobox_ai.llm.local import EchoLLM
from stobox_ai.moderation import StrikeBook, decide
from stobox_ai.moderation.detector import Moderator


# --------------------------------------------------------------------------- #
# Strikes ledger
# --------------------------------------------------------------------------- #
def test_strikebook_count_decay_pardon(tmp_path):
    book = StrikeBook(tmp_path / "s.json", decay_days=30)
    assert book.count("t:1") == 0
    assert book.add("t:1", "harassment") == 1
    assert book.add("t:1", "harassment") == 2
    assert book.add("t:1", "spam") == 1          # per-category count
    assert book.count("t:1") == 3                # total
    # Expire the harassment strikes by back-dating them.
    rec = book.record("t:1")
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    for s in rec.strikes:
        if s.category == "harassment":
            s.at = old
    assert book.count("t:1", "harassment") == 0  # decayed
    assert book.count("t:1", "spam") == 1
    # Pardon removes the most recent strike.
    book.pardon("t:1")
    assert book.count("t:1", "spam") == 0


def test_strikebook_persist_and_ban(tmp_path):
    book = StrikeBook(tmp_path / "s.json")
    book.add("t:9", "scam", display_name="Bad Actor")
    book.set_banned("t:9", True)
    book2 = StrikeBook(tmp_path / "s.json")
    assert book2.record("t:9").banned
    assert book2.count("t:9", "scam") == 1


# --------------------------------------------------------------------------- #
# Severity policy
# --------------------------------------------------------------------------- #
def test_policy_ladders():
    # Zero-tolerance: ban on first strike.
    assert decide("scam", 1).action == ModerationAction.BAN
    assert decide("phishing", 1).action == ModerationAction.BAN
    # Hate: mute then ban.
    assert decide("hate_slur", 1).action == ModerationAction.MUTE
    assert decide("hate_slur", 2).action == ModerationAction.BAN
    assert decide("hate_slur", 5).action == ModerationAction.BAN   # clamped
    # Harassment graduates.
    steps = [decide("harassment", i).action for i in range(1, 5)]
    assert steps == [ModerationAction.DELETE, ModerationAction.MUTE,
                     ModerationAction.MUTE, ModerationAction.BAN]
    # Defamation: two free passes (correct-don't-delete), then sanction.
    assert decide("defamation_team", 1).action == ModerationAction.NONE
    assert decide("defamation_team", 2).action == ModerationAction.NONE
    assert decide("defamation_team", 3).action == ModerationAction.MUTE
    # Unknown / safe → no action.
    assert decide("criticism", 1).action == ModerationAction.NONE


# --------------------------------------------------------------------------- #
# Detector (deterministic paths, offline)
# --------------------------------------------------------------------------- #
def _mod(config, tmp_path):
    return Moderator(config, EchoLLM(model="echo"), StrikeBook(tmp_path / "s.json"))


def _msg(text, name="Alice", uid="42", admin=False):
    return IncomingMessage(
        author=Author(external_id=uid, display_name=name, is_admin=admin),
        text=text, chat_id="grp", chat_type=ChatType.GROUP, message_id="1",
    )


@pytest.mark.asyncio
async def test_scam_pattern_bans(config, tmp_path):
    mod = _mod(config, tmp_path)
    v = await mod.evaluate(_msg("DM me to recover your funds, I'm from support"))
    assert v.category == "scam" and v.action == ModerationAction.BAN and v.delete
    assert v.alert_admin


@pytest.mark.asyncio
async def test_hard_slur_mutes_then_bans(config, tmp_path):
    mod = _mod(config, tmp_path)
    v1 = await mod.evaluate(_msg("you f4ggot", uid="7"))
    assert v1.category == "hate_slur" and v1.action == ModerationAction.MUTE
    v2 = await mod.evaluate(_msg("another slur n1gger", uid="7"))
    assert v2.action == ModerationAction.BAN            # second hate strike
    assert v2.strike_count == 2


@pytest.mark.asyncio
async def test_impersonation_name_alerts_without_autopunish(config, tmp_path):
    mod = _mod(config, tmp_path)
    v = await mod.evaluate(_msg("hey everyone", name="Stobox Support"))
    assert v.category == "impersonation" and v.action == ModerationAction.NONE
    assert v.alert_admin and v.flagged


@pytest.mark.asyncio
async def test_impersonation_with_bait_is_scam(config, tmp_path):
    mod = _mod(config, tmp_path)
    v = await mod.evaluate(_msg("DM me for help with your wallet", name="Stobox Admin"))
    assert v.category == "scam" and v.action == ModerationAction.BAN


@pytest.mark.asyncio
async def test_admins_and_casual_profanity_pass(config, tmp_path):
    mod = _mod(config, tmp_path)
    # Admin is never moderated.
    assert not (await mod.evaluate(_msg("f4ggot", admin=True))).flagged
    # Casual profanity, no target, no slur → deterministic layer clears it; the
    # echo classifier returns nothing → not flagged.
    assert not (await mod.evaluate(_msg("wtf this gas fee is insane lol"))).flagged
