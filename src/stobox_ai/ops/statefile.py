"""Atomic JSON state files — shared by every ``data/*.json`` ledger.

Two failure modes this module exists to close:

* **Torn writes** — a plain ``path.write_text(...)`` truncates the live file
  before the new bytes land; a crash (OOM, deploy, kill -9, disk-full) in that
  window corrupts the ledger. ``save_json_atomic`` writes to a temp file,
  fsyncs, then ``os.replace``s it into place — the live file is always either
  the old state or the new state, never half of each.

* **Silent resets** — loaders that swallow a parse error and continue with an
  empty dict quietly forget strikes, reminder ledgers, and XP. ``load_json_guarded``
  instead QUARANTINES a corrupt file aside (``<name>.corrupt-<utc-ts>``) so the
  evidence survives for recovery, logs loudly, and only then lets the caller
  start fresh.

Plus an optional **Postgres mirror** for ephemeral disks: files remain the
fast synchronous working store, but every atomic save also upserts the payload
into a ``bot_state`` table (fire-and-forget), and ``restore_state_files`` pulls
the mirrored payloads back onto disk at boot BEFORE any book loads. Result:
operational state survives a redeploy even on platforms with no volume
(Railway), with zero API change for the books.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Postgres state mirror (optional; enabled by init_state_mirror at boot).
# ---------------------------------------------------------------------------

_mirror_pool = None      # psycopg AsyncConnectionPool | None


async def init_state_mirror(database_url: str | None) -> bool:
    """Open the mirror (idempotent). No-op without a DATABASE_URL; any failure
    degrades to files-only, never blocks boot."""
    global _mirror_pool
    if not database_url or _mirror_pool is not None:
        return _mirror_pool is not None
    try:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(database_url, min_size=1, max_size=2,
                                   open=False, timeout=10)
        await pool.open()
        async with pool.connection() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS bot_state "
                "(name TEXT PRIMARY KEY, data JSONB NOT NULL, "
                " updated TIMESTAMPTZ DEFAULT now())"
            )
        _mirror_pool = pool
        log.info("state_mirror.ready")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("state_mirror.unavailable", error=str(exc))
        return False


async def restore_state_files(paths: list[str | Path]) -> int:
    """Boot-time restore: for each configured state file that the mirror holds,
    write the mirrored payload to disk so books load it as usual. A file
    already present locally is left alone (local disk is fresher — the mirror
    only lags by in-flight fire-and-forget writes)."""
    if _mirror_pool is None:
        return 0
    try:
        async with _mirror_pool.connection() as conn:
            cur = await conn.execute("SELECT name, data FROM bot_state")
            rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("state_mirror.restore_failed", error=str(exc))
        return 0
    mirrored = {name: data for name, data in rows}
    restored = 0
    for p in paths:
        path = Path(p)
        if path.exists() or path.name not in mirrored:
            continue
        try:
            save_json_atomic(path, mirrored[path.name], _mirror=False)
            restored += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("state_mirror.restore_write_failed", path=str(path), error=str(exc))
    if restored:
        log.info("state_mirror.restored", files=restored)
    return restored


async def close_state_mirror() -> None:
    global _mirror_pool
    if _mirror_pool is not None:
        try:
            await _mirror_pool.close()
        except Exception:  # noqa: BLE001
            pass
        _mirror_pool = None


async def _mirror_upsert(name: str, data: str) -> None:
    try:
        async with _mirror_pool.connection() as conn:
            await conn.execute(
                "INSERT INTO bot_state (name, data, updated) VALUES (%s, %s, now()) "
                "ON CONFLICT (name) DO UPDATE SET data=EXCLUDED.data, updated=now()",
                (name, data),
            )
    except Exception as exc:  # noqa: BLE001 - mirror is best-effort by design
        log.warning("state_mirror.upsert_failed", name=name, error=str(exc))


def _schedule_mirror(name: str, data: str) -> None:
    """Fire-and-forget mirror write. Called from SYNC save paths — only works
    when an event loop is running (the app runtime); CLI tools skip silently."""
    if _mirror_pool is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_mirror_upsert(name, data))
    # Keep a reference so the task isn't garbage-collected mid-flight.
    _mirror_tasks.add(task)
    task.add_done_callback(_mirror_tasks.discard)


_mirror_tasks: set[asyncio.Task] = set()


def save_json_atomic(path: str | Path, payload: Any, *, indent: int = 1,
                     _mirror: bool = True) -> None:
    """Serialize ``payload`` and atomically replace ``path`` with it, then
    mirror the payload to Postgres when the state mirror is enabled (keyed by
    file basename — stable across environments and tmp test dirs).

    Raises on file failure — callers keep their own try/except + log so a
    broken disk never crashes a handler, mirroring the previous behavior.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=indent)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if _mirror:
        _schedule_mirror(path.name, data)


def load_json_guarded(path: str | Path, *, label: str) -> Any | None:
    """Parse ``path`` as JSON. Returns None when the file is missing.

    A corrupt file is renamed to ``<name>.corrupt-<timestamp>`` (so the next
    save can't clobber the evidence) and None is returned — the caller starts
    fresh, and the incident is visible in the logs and on disk.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse/read failure quarantines
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        quarantine = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, quarantine)
            log.error(f"{label}.state_corrupt_quarantined",
                      path=str(path), quarantined=str(quarantine), error=str(exc))
        except OSError as move_exc:
            log.error(f"{label}.state_corrupt_unmovable",
                      path=str(path), error=str(exc), move_error=str(move_exc))
        return None
