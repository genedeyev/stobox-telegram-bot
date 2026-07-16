"""Decision log — the audit trail behind every AI action.

Each processed message emits one structured event: retrieval stats, reasoning
metadata, sources, latency, confidence, mode/persona, moderation action, lead
signals. Always logged via structlog; also persisted to Postgres when available.
An in-memory rolling window powers the /stats command and the daily digest
without a DB round-trip.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import Config, get_secrets
from ..logging import get_logger

log = get_logger("stobox_ai.decision")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Decision:
    at: datetime = field(default_factory=_now)
    channel: str = "telegram"
    chat_id: str = ""
    user_key: str = ""
    mode: str = ""
    persona: str = "auto"
    language: str = "en"
    question: str = ""
    confidence: str = "medium"
    confidence_score: float = 0.0
    retrieved: int = 0
    top_score: float = 0.0
    sources: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    moderation: str = "none"
    escalated: bool = False
    lead_captured: bool = False
    answered: bool = True
    tokens_in: int = 0
    tokens_out: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class DecisionLog:
    def __init__(self, pool=None, window: int = 5000) -> None:
        self._pool = pool
        self._ring: deque[Decision] = deque(maxlen=window)

    def records(self, last_n: int | None = None) -> list[Decision]:
        """Raw decisions from the in-memory ring — input to the insights layer."""
        items = list(self._ring)
        return items[-last_n:] if last_n else items

    async def backfill(self, limit: int | None = None) -> int:
        """Rehydrate the ring from Postgres at boot. Without this, every deploy
        reset the analytics window — the daily digest, weekly FAQ, and content
        flywheel only saw post-restart data."""
        if not self._pool:
            return 0
        limit = limit or self._ring.maxlen or 5000
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT data FROM decision_log ORDER BY id DESC LIMIT %s", (limit,)
                )
                rows = await cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("decision.backfill_failed", error=str(exc))
            return 0
        from ..util import filter_dataclass_kwargs

        restored = 0
        for (data,) in reversed(rows):           # oldest → newest into the ring
            try:
                d = data if isinstance(data, dict) else json.loads(data)
                d = filter_dataclass_kwargs(Decision, d)
                if isinstance(d.get("at"), str):
                    d["at"] = datetime.fromisoformat(d["at"])
                self._ring.append(Decision(**d))
                restored += 1
            except Exception:  # noqa: BLE001 - a malformed old row is skipped
                continue
        if restored:
            log.info("decision.backfilled", rows=restored)
        return restored

    async def prune(self, days: int = 90) -> int:
        """Retention: decision rows hold verbatim user questions (PII) — they
        must not accumulate forever."""
        if not self._pool:
            return 0
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "DELETE FROM decision_log WHERE at < now() - make_interval(days => %s)",
                    (days,),
                )
                removed = cur.rowcount or 0
            if removed:
                log.info("decision.pruned", removed=removed, retention_days=days)
            return removed
        except Exception as exc:  # noqa: BLE001
            log.warning("decision.prune_failed", error=str(exc))
            return 0

    async def purge_user(self, user_key: str) -> int:
        """GDPR erasure: remove this user's decisions from ring and Postgres."""
        kept = [d for d in self._ring if d.user_key != user_key]
        removed = len(self._ring) - len(kept)
        self._ring.clear()
        self._ring.extend(kept)
        if self._pool:
            try:
                async with self._pool.connection() as conn:
                    cur = await conn.execute(
                        "DELETE FROM decision_log WHERE data->>'user_key' = %s", (user_key,)
                    )
                    removed += cur.rowcount or 0
            except Exception as exc:  # noqa: BLE001
                log.warning("decision.purge_failed", error=str(exc))
        return removed

    async def record(self, d: Decision) -> None:
        self._ring.append(d)
        log.info(
            "decision",
            chat_id=d.chat_id,
            mode=d.mode,
            persona=d.persona,
            lang=d.language,
            confidence=d.confidence,
            score=round(d.confidence_score, 2),
            retrieved=d.retrieved,
            sources=d.sources,
            latency_ms=round(d.latency_ms, 1),
            moderation=d.moderation,
            escalated=d.escalated,
            lead=d.lead_captured,
            q=d.question[:120],
        )
        if self._pool:
            await self._persist(d)

    async def _persist(self, d: Decision) -> None:
        try:
            payload = asdict(d)
            payload["at"] = d.at.isoformat()
            async with self._pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO decision_log (at, data) VALUES (%s, %s)",
                    (d.at, json.dumps(payload, default=str)),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("decision.persist_failed", error=str(exc))

    def snapshot(self, last_n: int | None = None) -> dict[str, Any]:
        items = list(self._ring)
        if last_n:
            items = items[-last_n:]
        if not items:
            return {"count": 0}
        answered = [d for d in items if d.answered]
        low_conf = [d for d in items if d.confidence == "low"]
        latencies = sorted(d.latency_ms for d in items)
        modes = Counter(d.mode for d in items)
        langs = Counter(d.language for d in items)
        sources = Counter(s for d in items for s in d.sources)
        return {
            "count": len(items),
            "answered": len(answered),
            "unknown_rate": round(len(low_conf) / len(items), 3),
            "leads": sum(1 for d in items if d.lead_captured),
            "escalations": sum(1 for d in items if d.escalated),
            "moderation_actions": sum(1 for d in items if d.moderation != "none"),
            "p50_latency_ms": round(latencies[len(latencies) // 2], 1),
            "p95_latency_ms": round(latencies[int(len(latencies) * 0.95)], 1),
            "avg_confidence": round(sum(d.confidence_score for d in items) / len(items), 3),
            "top_modes": modes.most_common(5),
            "top_languages": langs.most_common(5),
            "top_sources": sources.most_common(8),
            "tokens_in": sum(d.tokens_in for d in items),
            "tokens_out": sum(d.tokens_out for d in items),
        }


async def build_decision_log(config: Config) -> DecisionLog:
    secrets = get_secrets()
    if secrets.database_url:
        pool = None
        try:
            from psycopg_pool import AsyncConnectionPool

            pool = AsyncConnectionPool(
                secrets.database_url, min_size=1, max_size=2, open=False, timeout=10
            )
            await pool.open()
            async with pool.connection() as conn:
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS decision_log "
                    "(id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ, data JSONB)"
                )
                # Retention pruning + digests query by time — without this
                # index every such query is a full table scan.
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS decision_at_idx ON decision_log(at)"
                )
            dlog = DecisionLog(pool)
            await dlog.backfill()
            return dlog
        except Exception as exc:  # noqa: BLE001
            log.warning("decision.pg_failed", error=str(exc))
            if pool is not None:
                try:
                    await pool.close()  # stop background reconnect spam
                except Exception:  # noqa: BLE001
                    pass
    return DecisionLog()
