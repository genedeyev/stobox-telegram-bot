"""Atomic JSON state files: torn-write safety + corrupt-file quarantine,
and the reminder book's per-recipient delivery ledger built on top of them."""

from __future__ import annotations

import json

from stobox_ai.ops.reminders import ReminderBook
from stobox_ai.ops.statefile import load_json_guarded, save_json_atomic


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    save_json_atomic(path, {"a": 1, "b": ["x", "y"]})
    assert load_json_guarded(path, label="t") == {"a": 1, "b": ["x", "y"]}


def test_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / "state.json"
    save_json_atomic(path, {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_missing_file_returns_none(tmp_path):
    assert load_json_guarded(tmp_path / "absent.json", label="t") is None


def test_corrupt_file_is_quarantined_not_silently_reset(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"truncated": ')   # simulates a crash mid-write
    assert load_json_guarded(path, label="t") is None
    # The live path is free for a fresh start…
    assert not path.exists()
    # …but the corrupt evidence is preserved on disk.
    quarantined = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == '{"truncated": '


def test_overwrite_replaces_content(tmp_path):
    path = tmp_path / "state.json"
    save_json_atomic(path, {"v": 1})
    save_json_atomic(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 2}


# --------------------------------------------------------------------------- #
# ReminderBook per-recipient delivery ledger
# --------------------------------------------------------------------------- #

def test_reminder_delivery_ledger_survives_restart(tmp_path):
    path = tmp_path / "reminders.json"
    book = ReminderBook(path)
    book.subscribe("111")
    book.subscribe("222")
    book.mark_delivered("burn-7", "111")     # crash before 222 got it

    reloaded = ReminderBook(path)
    assert reloaded.was_delivered("burn-7", "111")       # never re-DM'd
    assert not reloaded.was_delivered("burn-7", "222")   # still owed the blast
    assert not reloaded.was_sent("burn-7")               # tag stays open


def test_mark_sent_closes_tag_and_clears_progress(tmp_path):
    book = ReminderBook(tmp_path / "reminders.json")
    book.subscribe("111")
    book.mark_delivered("burn-7", "111")
    book.mark_sent("burn-7")
    assert book.was_sent("burn-7")
    assert book.delivered == {}
    # Persisted shape stays loadable.
    reloaded = ReminderBook(book.path)
    assert reloaded.was_sent("burn-7")


def test_legacy_reminder_state_still_loads(tmp_path):
    """Pre-ledger files (no 'delivered' key) must load unchanged."""
    path = tmp_path / "reminders.json"
    path.write_text(json.dumps({"subscribers": {"9": "en"}, "sent": ["burn-30"]}))
    book = ReminderBook(path)
    assert book.is_subscribed("9")
    assert book.was_sent("burn-30")
    assert book.delivered == {}
