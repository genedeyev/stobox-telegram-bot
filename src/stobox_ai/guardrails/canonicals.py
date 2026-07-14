"""Canonicals loader — the PR-gated guardrail facts.

canonicals.yaml is injected **verbatim** into the system prompt and overrides
retrieved content (precedence CANONICALS > FRESHNESS > retrieved). The pipeline
never edits it; it is changed only by human-reviewed PR.

Time-bombing: any fact carrying `valid_until` in the past is no longer asserted.
We keep the verbatim block for transparency but append an explicit runtime
override that (a) tells the model to stop asserting the expired fact and (b)
substitutes its `fallback` phrasing. Expired facts are also returned so the
caller can fire an admin alert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from ..logging import get_logger

log = get_logger(__name__)


def _today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).date()


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


@dataclass(slots=True)
class ExpiredFact:
    path: str            # dotted location in the yaml, e.g. tokens.stbu.migration
    valid_until: date
    fallback: str


@dataclass(slots=True)
class Canonicals:
    raw_text: str                        # verbatim file contents (for injection)
    data: dict[str, Any]                 # parsed tree (for freshness + lookups)
    version: str = ""
    expired: list[ExpiredFact] = field(default_factory=list)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def injection_block(self, now: datetime | None = None) -> str:
        """The verbatim canonicals plus any time-bomb override lines."""
        block = self.raw_text.strip()
        if not self.expired:
            return block
        overrides = ["", "### RUNTIME OVERRIDE — expired canonical facts (do NOT assert):"]
        for e in self.expired:
            overrides.append(
                f"- `{e.path}` expired {e.valid_until.isoformat()}. "
                f"Stop asserting it. Use instead: {e.fallback}"
            )
        return block + "\n" + "\n".join(overrides)

    def scan_expired(self, now: datetime | None = None) -> list[ExpiredFact]:
        today = _today(now)
        found: list[ExpiredFact] = []

        def walk(node: Any, path: str) -> None:
            if not isinstance(node, dict):
                return
            if "valid_until" in node:
                vu = _as_date(node["valid_until"])
                if vu and vu < today:
                    found.append(
                        ExpiredFact(
                            path=path or "(root)",
                            valid_until=vu,
                            fallback=str(node.get("fallback", "Check stobox.io for current status.")),
                        )
                    )
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)

        walk(self.data, "")
        return found


def load_canonicals(path: str | Path = "canonicals.yaml", now: datetime | None = None) -> Canonicals:
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    canon = Canonicals(raw_text=raw, data=data, version=str(data.get("version", "")))
    canon.expired = canon.scan_expired(now)
    if canon.expired:
        log.warning(
            "canonicals.expired",
            facts=[e.path for e in canon.expired],
            version=canon.version,
        )
    return canon
