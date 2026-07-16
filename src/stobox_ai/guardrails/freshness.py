"""[FRESHNESS] block — auto-assembled at answer time.

Contains: current UTC date, knowledge-index last-sync timestamp + corpus hash,
5 latest blog posts, the current Eqvista valuation mark, and the STBU migration
phase computed from the canonical dates. This is how the bot can honestly state
how fresh it is and give the correct migration-countdown framing.

The valuation mark is NOT hardcoded in canonicals: it comes from a data file /
env (mirroring the site's `src/data/valuation.ts`), or falls back to pointing at
stobox.io/valuation.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from ..logging import get_logger
from .canonicals import Canonicals, _as_date

log = get_logger(__name__)


class MigrationPhase(str, enum.Enum):
    PRE = "pre-migration"
    BURN_OPEN = "burn-window-open"
    BURN_CLOSED = "burn-window-closed"
    CLAIMS_OPEN = "claims-open"


def _fmt(d: date) -> str:
    return d.strftime("%d %B %Y")


def compute_migration_phase(
    canon: Canonicals, now: datetime | None = None
) -> tuple[MigrationPhase, str]:
    now = now or datetime.now(UTC)
    today = now.date()
    m = canon.get("tokens.stbu.migration", {}) or {}
    starts = _as_date(m.get("burn_window_opens")) or _as_date(m.get("burns_count_from"))
    deadline = _as_date(m.get("burn_deadline"))
    claim = _as_date(m.get("claim_opens"))

    if starts and today < starts:
        return MigrationPhase.PRE, f"Burn window opens {_fmt(starts)}; not open yet."
    if deadline and today <= deadline:
        return (
            MigrationPhase.BURN_OPEN,
            f"Burn window is OPEN. Deadline: {_fmt(deadline)} 23:59 UTC. "
            f"Burn-and-mint, 1:1, same-wallet only, destination chain Base.",
        )
    if claim and today < claim:
        return (
            MigrationPhase.BURN_CLOSED,
            f"Burn deadline has passed; claims open {_fmt(claim)}.",
        )
    if claim and today >= claim:
        return MigrationPhase.CLAIMS_OPEN, f"Claims are open (since {_fmt(claim)})."
    return MigrationPhase.PRE, "Migration status: see stobox.io for current details."


@dataclass(slots=True)
class FreshnessBuilder:
    canon: Canonicals
    last_sync: datetime | None = None
    corpus_hash: str = ""
    blog_posts: list[dict[str, Any]] | None = None   # [{title, url, date}]
    valuation_mark: str | None = None

    @staticmethod
    def valuation_from_env() -> str | None:
        return os.environ.get("STOBOX_VALUATION") or None

    def build(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        phase, phase_text = compute_migration_phase(self.canon, now)

        lines = ["## [FRESHNESS] — live runtime state", ""]
        lines.append(f"- Current date (UTC): {now.strftime('%d %B %Y')}")
        if self.last_sync:
            lines.append(
                f"- Knowledge index last synced: {self.last_sync.strftime('%d %B %Y %H:%M UTC')}"
                + (f" (corpus {self.corpus_hash[:12]})" if self.corpus_hash else "")
            )
        else:
            lines.append("- Knowledge index last synced: unknown")

        valuation = self.valuation_mark or self.valuation_from_env()
        if valuation:
            lines.append(
                f"- Eqvista company valuation mark: {valuation} "
                "(a COMPANY valuation — not the STBX token price, not an offer). "
                "See https://stobox.io/valuation"
            )
        else:
            lines.append(
                "- Eqvista company valuation: see https://stobox.io/valuation for the "
                "current mark (do not state a number you were not given)."
            )

        lines.append(f"- STBU migration phase: {phase.value} — {phase_text}")
        if self.canon.expired:
            lines.append(
                "- ⚠️ Some canonical time-limited facts have expired; use the runtime "
                "overrides in [CANONICALS] and prefer stobox.io for current status."
            )

        if self.blog_posts:
            lines.append("- Latest blog posts:")
            for p in self.blog_posts[:5]:
                d = p.get("date")
                lines.append(f"    • {p.get('title', 'Post')}{f' ({d})' if d else ''} — {p.get('url', '')}")
        return "\n".join(lines)
