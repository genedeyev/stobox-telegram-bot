"""Milestone shout-outs: XP level-up detection (offline)."""

from __future__ import annotations

from stobox_ai.engagement.xp import XPBook, level_for


def test_level_for_thresholds():
    assert level_for(0) == (0, "Newcomer")
    assert level_for(60)[1] == "Explorer"
    assert level_for(1500)[1] == "Community OG"


def test_check_levelup_fires_once_per_level(tmp_path):
    b = XPBook(tmp_path / "xp.json")
    b.award("u1", 60, display_name="Gene")            # → Explorer
    assert b.check_levelup("u1") == "Explorer"
    assert b.check_levelup("u1") is None              # already celebrated
    b.award("u1", 100)                                # 160 → Regular
    assert b.check_levelup("u1") == "Regular"
    assert b.check_levelup("u1") is None


def test_check_levelup_none_below_threshold(tmp_path):
    b = XPBook(tmp_path / "xp.json")
    b.award("u1", 10)                                 # still Newcomer (level 0)
    assert b.check_levelup("u1") is None


def test_notified_level_persists(tmp_path):
    path = tmp_path / "xp.json"
    b1 = XPBook(path)
    b1.award("u1", 60)
    assert b1.check_levelup("u1") == "Explorer"
    b2 = XPBook(path)                                 # reload
    assert b2.check_levelup("u1") is None             # persisted, not re-celebrated
