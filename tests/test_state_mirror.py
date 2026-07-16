"""Postgres state mirror: files stay the working store; the mirror is the
durability net for ephemeral disks. Offline tests via a fake pool."""

from __future__ import annotations

import asyncio
import json

import pytest

from stobox_ai.ops import statefile


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, store: dict):
        self.store = store

    async def execute(self, sql, params=None):
        if sql.strip().startswith("INSERT INTO bot_state"):
            name, data = params
            self.store[name] = data
            return _FakeCursor([])
        if "SELECT name, data FROM bot_state" in sql:
            return _FakeCursor([(k, json.loads(v)) for k, v in self.store.items()])
        return _FakeCursor([])


class _FakePool:
    def __init__(self, store: dict):
        self._store = store

    def connection(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return _FakeConn(pool._store)

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.fixture
def mirror(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(statefile, "_mirror_pool", _FakePool(store))
    return store


async def test_save_mirrors_payload(mirror, tmp_path):
    statefile.save_json_atomic(tmp_path / "xp.json", {"u": 1})
    await asyncio.gather(*statefile._mirror_tasks)      # flush fire-and-forget
    assert json.loads(mirror["xp.json"]) == {"u": 1}


async def test_restore_writes_missing_files_only(mirror, tmp_path):
    mirror["reminders.json"] = json.dumps({"subscribers": {"1": "en"}})
    mirror["xp.json"] = json.dumps({"u": 5})
    existing = tmp_path / "xp.json"
    existing.write_text('{"u": 99}')                    # local disk is fresher

    restored = await statefile.restore_state_files(
        [tmp_path / "reminders.json", existing, tmp_path / "absent_elsewhere.json"]
    )
    assert restored == 1
    assert json.loads((tmp_path / "reminders.json").read_text()) == {
        "subscribers": {"1": "en"}
    }
    assert json.loads(existing.read_text()) == {"u": 99}   # untouched


async def test_mirror_disabled_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(statefile, "_mirror_pool", None)
    statefile.save_json_atomic(tmp_path / "s.json", {"a": 1})   # no crash, file written
    assert (tmp_path / "s.json").exists()
    assert await statefile.restore_state_files([tmp_path / "s.json"]) == 0


async def test_end_to_end_redeploy_simulation(mirror, tmp_path):
    """Book saves → 'disk wiped' → restore → a fresh book sees the state."""
    from stobox_ai.ops.reminders import ReminderBook

    book = ReminderBook(tmp_path / "deploy1" / "reminders.json")
    book.subscribe("42")
    book.mark_sent("burn-30")
    await asyncio.gather(*statefile._mirror_tasks)

    fresh_dir = tmp_path / "deploy2"                    # new container, empty disk
    await statefile.restore_state_files([fresh_dir / "reminders.json"])
    reborn = ReminderBook(fresh_dir / "reminders.json")
    assert reborn.is_subscribed("42")
    assert reborn.was_sent("burn-30")
