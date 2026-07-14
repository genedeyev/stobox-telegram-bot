"""Vector store abstraction.

Two interchangeable backends:
  * :class:`PgVectorStore`   — Postgres + pgvector (production).
  * :class:`InMemoryVectorStore` — numpy cosine search (dev/tests, no infra).

Both persist chunk text + metadata so retrieval can build BM25 and cite sources.
The store also tracks a monotonically increasing ``version`` bumped on every
write, which the retrieval layer uses to invalidate its cached BM25 index.
"""

from __future__ import annotations

import abc
import json
from dataclasses import asdict

from ..logging import get_logger
from .models import Chunk, DocMeta

log = get_logger(__name__)


def _meta_to_json(meta: DocMeta | None) -> str:
    if meta is None:
        return "{}"
    d = asdict(meta)
    if d.get("doc_date") is not None:
        d["doc_date"] = str(d["doc_date"])
    return json.dumps(d)


def _meta_from_json(raw: str | dict) -> DocMeta:
    d = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    d.pop("doc_date", None)  # not needed for citation rendering
    known = {f for f in DocMeta.__slots__}  # type: ignore[attr-defined]
    filtered = {k: v for k, v in d.items() if k in known and k != "extra"}
    filtered.setdefault("title", "Untitled")
    filtered.setdefault("source_file", "")
    return DocMeta(**filtered, extra=d.get("extra", {}))


class VectorStore(abc.ABC):
    version: int = 0

    @abc.abstractmethod
    async def upsert(self, chunks: list[Chunk]) -> None: ...

    @abc.abstractmethod
    async def delete_doc(self, doc_id: str) -> None: ...

    @abc.abstractmethod
    async def search(self, embedding: list[float], top_k: int) -> list[tuple[Chunk, float]]: ...

    @abc.abstractmethod
    async def all_chunks(self) -> list[Chunk]: ...

    @abc.abstractmethod
    async def doc_hashes(self) -> dict[str, str]: ...

    @abc.abstractmethod
    async def clear(self) -> None: ...

    async def count(self) -> int:
        return len(await self.all_chunks())


# --------------------------------------------------------------------------- #
# In-memory (numpy) — the always-available fallback.
# --------------------------------------------------------------------------- #
class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._doc_hash: dict[str, str] = {}

    async def upsert(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c
        self.version += 1

    def register_doc_hash(self, doc_id: str, content_hash: str) -> None:
        self._doc_hash[doc_id] = content_hash

    async def delete_doc(self, doc_id: str) -> None:
        self._chunks = {k: v for k, v in self._chunks.items() if v.doc_id != doc_id}
        self._doc_hash.pop(doc_id, None)
        self.version += 1

    async def search(self, embedding: list[float], top_k: int) -> list[tuple[Chunk, float]]:
        import numpy as np

        items = [c for c in self._chunks.values() if c.embedding]
        if not items:
            return []
        mat = np.array([c.embedding for c in items], dtype="float32")
        q = np.array(embedding, dtype="float32")
        qn = np.linalg.norm(q) or 1.0
        mn = np.linalg.norm(mat, axis=1)
        mn[mn == 0] = 1.0
        sims = (mat @ q) / (mn * qn)
        order = sims.argsort()[::-1][:top_k]
        return [(items[i], float(sims[i])) for i in order]

    async def all_chunks(self) -> list[Chunk]:
        return list(self._chunks.values())

    async def doc_hashes(self) -> dict[str, str]:
        return dict(self._doc_hash)

    async def clear(self) -> None:
        self._chunks.clear()
        self._doc_hash.clear()
        self.version += 1


# --------------------------------------------------------------------------- #
# Postgres + pgvector — production backend.
# --------------------------------------------------------------------------- #
class PgVectorStore(VectorStore):
    def __init__(self, pool, dimensions: int) -> None:
        self._pool = pool
        self._dim = dimensions

    @classmethod
    async def create(cls, database_url: str, dimensions: int) -> PgVectorStore:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(database_url, min_size=1, max_size=8, open=False)
        await pool.open()
        store = cls(pool, dimensions)
        await store._init_schema()
        return store

    async def _init_schema(self) -> None:
        from pgvector.psycopg import register_vector_async

        async with self._pool.connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await register_vector_async(conn)
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS kb_chunks (
                    chunk_id     TEXT PRIMARY KEY,
                    doc_id       TEXT NOT NULL,
                    content_hash TEXT,
                    ordinal      INT,
                    section      TEXT,
                    text         TEXT NOT NULL,
                    summary      TEXT,
                    keywords     TEXT[],
                    embedding    vector({self._dim}),
                    meta         JSONB
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS kb_doc_idx ON kb_chunks(doc_id)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS kb_vec_idx ON kb_chunks "
                "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
            )

    async def upsert(self, chunks: list[Chunk]) -> None:
        from pgvector.psycopg import register_vector_async

        async with self._pool.connection() as conn:
            await register_vector_async(conn)
            for c in chunks:
                await conn.execute(
                    """
                    INSERT INTO kb_chunks
                        (chunk_id, doc_id, content_hash, ordinal, section, text,
                         summary, keywords, embedding, meta)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        text=EXCLUDED.text, summary=EXCLUDED.summary,
                        keywords=EXCLUDED.keywords, embedding=EXCLUDED.embedding,
                        meta=EXCLUDED.meta
                    """,
                    (
                        c.chunk_id, c.doc_id,
                        c.meta.extra.get("content_hash") if c.meta else None,
                        c.ordinal, c.section, c.text, c.summary, c.keywords,
                        c.embedding, _meta_to_json(c.meta),
                    ),
                )
        self.version += 1

    async def delete_doc(self, doc_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM kb_chunks WHERE doc_id=%s", (doc_id,))
        self.version += 1

    async def search(self, embedding: list[float], top_k: int) -> list[tuple[Chunk, float]]:
        from pgvector.psycopg import register_vector_async

        async with self._pool.connection() as conn:
            await register_vector_async(conn)
            cur = await conn.execute(
                """
                SELECT doc_id, section, text, summary, keywords, ordinal, meta,
                       1 - (embedding <=> %s) AS score
                FROM kb_chunks
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (embedding, embedding, top_k),
            )
            rows = await cur.fetchall()
        return [(self._row_to_chunk(r), float(r[7])) for r in rows]

    async def all_chunks(self) -> list[Chunk]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, section, text, summary, keywords, ordinal, meta FROM kb_chunks"
            )
            rows = await cur.fetchall()
        return [self._row_to_chunk(r) for r in rows]

    async def doc_hashes(self) -> dict[str, str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT doc_id, MAX(content_hash) FROM kb_chunks GROUP BY doc_id"
            )
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows if r[1]}

    async def clear(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("TRUNCATE kb_chunks")
        self.version += 1

    @staticmethod
    def _row_to_chunk(r) -> Chunk:
        return Chunk(
            doc_id=r[0], section=r[1], text=r[2], summary=r[3],
            keywords=list(r[4] or []), ordinal=r[5], meta=_meta_from_json(r[6]),
        )
