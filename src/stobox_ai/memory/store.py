"""Memory store.

Postgres-backed when ``DATABASE_URL`` is set (durable long-term memory), with an
in-memory fallback for dev/tests. Conversation memory is a bounded ring per
(chat, thread); long-term memory is a per-user profile persisted as JSON.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime

from ..config import Config, get_secrets
from ..logging import get_logger
from .models import ConversationTurn, UserProfile

log = get_logger(__name__)


def _profile_to_json(p: UserProfile) -> str:
    d = asdict(p)
    d["first_seen"] = p.first_seen.isoformat()
    d["last_interaction"] = p.last_interaction.isoformat()
    return json.dumps(d)


def _profile_from_json(raw: str | dict) -> UserProfile:
    d = raw if isinstance(raw, dict) else json.loads(raw)
    d["first_seen"] = datetime.fromisoformat(d["first_seen"])
    d["last_interaction"] = datetime.fromisoformat(d["last_interaction"])
    return UserProfile(**d)


class MemoryStore:
    """Base = in-memory implementation; Pg subclass overrides persistence."""

    def __init__(self, window: int = 12) -> None:
        self.window = window
        self._convos: dict[str, deque[ConversationTurn]] = defaultdict(
            lambda: deque(maxlen=self.window)
        )
        self._profiles: dict[str, UserProfile] = {}

    # --- conversation memory -------------------------------------------------
    def add_turn(self, thread_key: str, role: str, text: str, name: str | None = None) -> None:
        self._convos[thread_key].append(ConversationTurn(role=role, text=text, name=name))

    def history(self, thread_key: str) -> list[ConversationTurn]:
        return list(self._convos.get(thread_key, ()))

    def last_activity(self, thread_key: str) -> datetime | None:
        turns = self._convos.get(thread_key)
        return turns[-1].at if turns else None

    # --- long-term user memory ----------------------------------------------
    async def get_profile(self, user_key: str, display_name: str | None = None) -> UserProfile:
        if user_key not in self._profiles:
            self._profiles[user_key] = UserProfile(user_key=user_key, display_name=display_name)
        return self._profiles[user_key]

    async def save_profile(self, profile: UserProfile) -> None:
        self._profiles[profile.user_key] = profile

    async def close(self) -> None:  # pragma: no cover - symmetry with Pg
        pass


class PgMemoryStore(MemoryStore):
    def __init__(self, pool, window: int = 12) -> None:
        super().__init__(window)
        self._pool = pool

    @classmethod
    async def create(cls, database_url: str, window: int) -> PgMemoryStore:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(database_url, min_size=1, max_size=4, open=False, timeout=10)
        await pool.open()
        try:
            store = cls(pool, window)
            async with pool.connection() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        user_key TEXT PRIMARY KEY,
                        data     JSONB NOT NULL,
                        updated  TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
        except Exception:
            await pool.close()  # stop background reconnect spam after fallback
            raise
        return store

    async def get_profile(self, user_key: str, display_name: str | None = None) -> UserProfile:
        if user_key in self._profiles:
            return self._profiles[user_key]
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT data FROM user_profiles WHERE user_key=%s", (user_key,)
            )
            row = await cur.fetchone()
        profile = (
            _profile_from_json(row[0])
            if row
            else UserProfile(user_key=user_key, display_name=display_name)
        )
        self._profiles[user_key] = profile
        return profile

    async def save_profile(self, profile: UserProfile) -> None:
        self._profiles[profile.user_key] = profile
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO user_profiles (user_key, data, updated)
                VALUES (%s, %s, now())
                ON CONFLICT (user_key) DO UPDATE SET data=EXCLUDED.data, updated=now()
                """,
                (profile.user_key, _profile_to_json(profile)),
            )


async def build_memory_store(config: Config) -> MemoryStore:
    window = int(config.get("memory.conversation_window", 12))
    secrets = get_secrets()
    if secrets.database_url:
        try:
            store = await PgMemoryStore.create(secrets.database_url, window)
            log.info("memory.pg")
            return store
        except Exception as exc:  # noqa: BLE001
            log.error("memory.pg_failed", error=str(exc))
    log.warning("memory.in_memory", reason="no/failed DATABASE_URL — dev fallback")
    return MemoryStore(window)
