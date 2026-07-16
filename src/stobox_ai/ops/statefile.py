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
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logging import get_logger

log = get_logger(__name__)


def save_json_atomic(path: str | Path, payload: Any, *, indent: int = 1) -> None:
    """Serialize ``payload`` and atomically replace ``path`` with it.

    Raises on failure — callers keep their own try/except + log so a broken
    disk never crashes a handler, mirroring the previous behavior.
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
