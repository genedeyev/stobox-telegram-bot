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
def _mod(config, tmp_path, *, enforce=True):
    """Default enforce=True so the enforcement LOGIC stays tested (it runs when
    moderation.enforce is on). Production defaults to coexist (enforce=False,
    ChatKeeper moderates) — covered by the coexist tests below."""
    m = Moderator(config, EchoLLM(model="echo"), StrikeBook(tmp_path / "s.json"))
    m.enforce = enforce
    m.link_policy_enabled = enforce   # link removal is part of enforcement
    return m


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


# --------------------------------------------------------------------------- #
# Link allowlist: official Stobox links stay, others are removed, admins exempt
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_official_stobox_link_from_user_is_kept(config, tmp_path):
    mod = _mod(config, tmp_path)
    for text in [
        "great read https://www.stobox.io/blog/x",
        "migrate at app.stobox.io/migrate",
        "join t.me/stobox_community",
        "our X https://x.com/StoboxCompany",
    ]:
        v = await mod.evaluate(_msg(text))
        assert v.action == ModerationAction.NONE, f"official link deleted: {text}"


@pytest.mark.asyncio
async def test_non_official_link_from_user_is_deleted(config, tmp_path):
    mod = _mod(config, tmp_path)
    v = await mod.evaluate(_msg("check this out https://example.com/great-deal"))
    assert v.category == "external_link"
    assert v.action == ModerationAction.DELETE and v.delete
    assert not v.alert_admin                 # quiet hygiene, not an admin ping
    assert "official Stobox links" in v.dm_text


@pytest.mark.asyncio
async def test_bare_domain_scam_link_deleted(config, tmp_path):
    mod = _mod(config, tmp_path)
    v = await mod.evaluate(_msg("sync your wallet at wallet-validate.io"))
    assert v.category == "external_link" and v.delete


@pytest.mark.asyncio
async def test_admin_may_post_any_link(config, tmp_path):
    mod = _mod(config, tmp_path)
    v = await mod.evaluate(_msg("here's a news piece https://coindesk.com/x", admin=True))
    assert v.action == ModerationAction.NONE   # Gene/Arevik exempt


@pytest.mark.asyncio
async def test_scam_link_still_bans_not_just_deletes(config, tmp_path):
    """A phishing link is caught by the scam ladder (ban), not the softer
    external_link rule."""
    mod = _mod(config, tmp_path)
    v = await mod.evaluate(_msg("claim your airdrop, connect wallet at https://evil.io"))
    assert v.category == "scam" and v.action == ModerationAction.BAN


@pytest.mark.asyncio
async def test_trusted_third_party_links_allowed_subdomains_too(config, tmp_path):
    """Gene-confirmed allowlist (explorers, market data, news) — domain +
    subdomains pass for regular members; lookalikes still don't."""
    mod = _mod(config, tmp_path)
    cases = [
        "verify on etherscan.io/tx/0xabc",
        "sepolia.etherscan.io/address/0x1",
        "price https://www.coingecko.com/en/coins/stobox-token",
        "read https://www.coindesk.com/markets/x",
        "data at rwa.xyz/protocols",
        "base explorer basescan.org/token/0x",
    ]
    for i, text in enumerate(cases):     # distinct uids so we don't trip flood
        v = await mod.evaluate(_msg(text, uid=f"u{i}"))
        assert v.action == ModerationAction.NONE, f"trusted link deleted: {text}"


@pytest.mark.asyncio
async def test_lookalike_of_trusted_domain_still_deleted(config, tmp_path):
    mod = _mod(config, tmp_path)
    for text in ["scam https://scam-etherscan.io/x", "fake etherscan.io.evil.com/x"]:
        v = await mod.evaluate(_msg(text))
        assert v.category == "external_link" and v.delete, f"lookalike allowed: {text}"


# --------------------------------------------------------------------------- #
# COEXIST mode (production default): ChatKeeper enforces; Stoby only detects.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_coexist_does_not_delete_links_or_ban(config, tmp_path):
    mod = _mod(config, tmp_path, enforce=False)
    # Non-official link — NOT deleted (ChatKeeper's job; Arevik's rule).
    v = await mod.evaluate(_msg("check https://example.com/x"))
    assert v.action == ModerationAction.NONE and not v.delete
    # A slur — Stoby does not mute/ban in coexist mode.
    v2 = await mod.evaluate(_msg("you f4ggot", uid="9"))
    assert v2.action == ModerationAction.NONE and not v2.delete


@pytest.mark.asyncio
async def test_coexist_does_nothing_on_scam(config, tmp_path):
    """Arevik's directive: on scam, Stoby does NOTHING (not even an admin ping) —
    ChatKeeper's filters handle it, and Stoby's detection deleted relevant msgs."""
    mod = _mod(config, tmp_path, enforce=False)
    v = await mod.evaluate(_msg("DM me to recover your funds, I'm from support"))
    assert v.action == ModerationAction.NONE and not v.alert_admin and not v.flagged


@pytest.mark.asyncio
async def test_admin_impersonator_banned_even_in_coexist(config, tmp_path):
    """The ONE enforcement Stoby keeps in coexist: a non-admin whose display
    name copies an admin is banned + deleted immediately (Arevik)."""
    mod = _mod(config, tmp_path, enforce=False)
    v = await mod.evaluate(_msg("hey friends, DM me for help",
                                name="Arevik | Support @ Stobox", uid="666"))
    assert v.category == "admin_impersonation"
    assert v.action == ModerationAction.BAN and v.delete and v.alert_admin
    # A prefixed/variant copy is also caught.
    v2 = await mod.evaluate(_msg("hi", name="Ross Shemeliak (Stobox)", uid="667"))
    assert v2.action == ModerationAction.BAN


@pytest.mark.asyncio
async def test_real_admin_with_admin_name_not_banned(config, tmp_path):
    """The real admin (verified by ID) posting under their own name is never
    flagged — they're exempt at the top of evaluate()."""
    mod = _mod(config, tmp_path, enforce=False)
    v = await mod.evaluate(_msg("hello team", name="Arevik | Support @ Stobox",
                                uid="111", admin=True))
    assert v.action == ModerationAction.NONE and not v.flagged
