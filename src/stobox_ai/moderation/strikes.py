"""Strikes ledger — progressive discipline memory.

Per-user, per-category record of moderation offenses with time decay, so the
policy can escalate (warn → mute → ban) across messages instead of judging each
in isolation. Persists to JSON (mount /app/data as a volume in prod), so a
restart doesn't wipe a repeat offender's history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)


@dataclass
class Strike:
    category: str
    at: str                       # ISO timestamp
    chat_id: str = ""
    excerpt: str = ""             # short redacted context for the mod-log


@dataclass
class UserRecord:
    user_key: str
    display_name: str = ""
    strikes: list[Strike] = field(default_factory=list)
    banned: bool = False
    muted_until: str = ""         # ISO; informational (Telegram enforces)


class StrikeBook:
    def __init__(self, state_path: str | Path = "data/strikes.json", decay_days: int = 30) -> None:
        self.path = Path(state_path)
        self.decay = timedelta(days=decay_days)
        self.users: dict[str, UserRecord] = {}
        self._load()

    # ------------------------------------------------------------------ #
    def _active(self, rec: UserRecord) -> list[Strike]:
        cutoff = datetime.now(UTC) - self.decay
        return [s for s in rec.strikes if _parse(s.at) >= cutoff]

    def count(self, user_key: str, category: str | None = None) -> int:
        """Active strikes — for one category, or total if category is None."""
        rec = self.users.get(user_key)
        if not rec:
            return 0
        active = self._active(rec)
        if category is None:
            return len(active)
        return sum(1 for s in active if s.category == category)

    def add(self, user_key: str, category: str, *, display_name: str = "",
            chat_id: str = "", excerpt: str = "") -> int:
        """Record a strike; returns the new active count for that category."""
        rec = self.users.setdefault(user_key, UserRecord(user_key=user_key))
        if display_name:
            rec.display_name = display_name
        rec.strikes.append(Strike(
            category=category, at=datetime.now(UTC).isoformat(),
            chat_id=chat_id, excerpt=excerpt[:160],
        ))
        self._save()
        return self.count(user_key, category)

    def pardon(self, user_key: str) -> bool:
        """Remove the most recent strike and lift any ban flag. Returns True if
        anything changed."""
        rec = self.users.get(user_key)
        if not rec:
            return False
        changed = False
        if rec.strikes:
            rec.strikes.pop()
            changed = True
        if rec.banned:
            rec.banned = False
            changed = True
        rec.muted_until = ""
        if changed:
            self._save()
        return changed

    def clear(self, user_key: str) -> bool:
        rec = self.users.get(user_key)
        if not rec:
            return False
        rec.strikes.clear()
        rec.banned = False
        rec.muted_until = ""
        self._save()
        return True

    def set_banned(self, user_key: str, banned: bool, display_name: str = "") -> None:
        rec = self.users.setdefault(user_key, UserRecord(user_key=user_key))
        if display_name:
            rec.display_name = display_name
        rec.banned = banned
        self._save()

    def set_muted(self, user_key: str, until_iso: str) -> None:
        rec = self.users.setdefault(user_key, UserRecord(user_key=user_key))
        rec.muted_until = until_iso
        self._save()

    def record(self, user_key: str) -> UserRecord | None:
        return self.users.get(user_key)

    def stats(self) -> dict:
        total = sum(len(self._active(r)) for r in self.users.values())
        banned = sum(1 for r in self.users.values() if r.banned)
        by_cat: dict[str, int] = {}
        for r in self.users.values():
            for s in self._active(r):
                by_cat[s.category] = by_cat.get(s.category, 0) + 1
        return {"users_with_strikes": sum(1 for r in self.users.values() if self._active(r)),
                "active_strikes": total, "banned": banned, "by_category": by_cat}

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        from ..ops.statefile import load_json_guarded

        data = load_json_guarded(self.path, label="strikes")
        if data is None:
            return
        try:
            for k, v in data.items():
                strikes = [Strike(**s) for s in v.get("strikes", [])]
                self.users[k] = UserRecord(
                    user_key=k, display_name=v.get("display_name", ""),
                    strikes=strikes, banned=v.get("banned", False),
                    muted_until=v.get("muted_until", ""),
                )
        except Exception as exc:  # noqa: BLE001
            log.error("strikes.load_failed", error=str(exc))

    def _save(self) -> None:
        from ..ops.statefile import save_json_atomic

        try:
            out = {
                k: {"display_name": r.display_name, "banned": r.banned,
                    "muted_until": r.muted_until,
                    "strikes": [vars(s) for s in r.strikes]}
                for k, r in self.users.items()
            }
            save_json_atomic(self.path, out)
        except Exception as exc:  # noqa: BLE001
            log.error("strikes.save_failed", error=str(exc))


def _parse(iso: str) -> datetime:
    try:
        d = datetime.fromisoformat(iso)
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)
