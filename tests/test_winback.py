"""Win-back book tests (offline, tmp-file state)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stobox_ai.ops.winback import WinBackBook


def test_can_nudge_when_never_nudged(tmp_path):
    b = WinBackBook(tmp_path / "wb.json")
    assert b.can_nudge("1", cooldown_days=45) is True


def test_cooldown_blocks_then_allows(tmp_path):
    b = WinBackBook(tmp_path / "wb.json")
    b.mark_nudged("1")
    assert b.can_nudge("1", cooldown_days=45) is False    # just nudged
    # Simulate a nudge 50 days ago → cooldown elapsed.
    old = (datetime.now(UTC) - timedelta(days=50)).isoformat()
    b.last_nudged["1"] = old
    assert b.can_nudge("1", cooldown_days=45) is True


def test_corrupt_timestamp_allows_nudge(tmp_path):
    b = WinBackBook(tmp_path / "wb.json")
    b.last_nudged["1"] = "not-a-date"
    assert b.can_nudge("1", cooldown_days=45) is True


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "wb.json"
    b1 = WinBackBook(path)
    b1.mark_nudged("42")
    b2 = WinBackBook(path)
    assert "42" in b2.last_nudged
    assert b2.can_nudge("42", cooldown_days=45) is False
