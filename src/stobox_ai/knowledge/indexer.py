"""Indexer — orchestrates load → semantic chunk → embed → store.

Incremental by content hash: unchanged documents are skipped; changed ones are
fully re-chunked and re-embedded (delete-then-insert), so edits never leave
stale chunks. Optional one-line LLM summaries per chunk aid retrieval/citation.
"""

from __future__ import annotations

from ..config import Config, get_secrets
from ..llm import build_embedder
from ..llm.base import ChatMessage
from ..logging import get_logger
from .chunking import SemanticChunker
from .ingest import SUPPORTED, load_directory, load_document
from .models import Document
from .store import InMemoryVectorStore, PgVectorStore, VectorStore

log = get_logger(__name__)


class Indexer:
    def __init__(
        self,
        store: VectorStore,
        embedder,
        chunker: SemanticChunker,
        summarize: bool = False,
        reasoner=None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.chunker = chunker
        self.summarize = summarize
        self.reasoner = reasoner

    @classmethod
    async def create(cls, config: Config) -> Indexer:
        store = await build_store(config)
        embedder = build_embedder(config)
        ch = config.section("knowledge.chunking")
        chunker = SemanticChunker(
            target_tokens=int(ch.get("target_tokens", 400)),
            max_tokens=int(ch.get("max_tokens", 800)),
            overlap_tokens=int(ch.get("overlap_tokens", 60)),
        )
        return cls(store, embedder, chunker)

    async def index_directory(self, path: str, rebuild: bool = False) -> int:
        if rebuild:
            await self.store.clear()
        existing = await self.store.doc_hashes()
        total = 0
        seen: set[str] = set()
        for doc in load_directory(path):
            seen.add(doc.doc_id)
            if not rebuild and existing.get(doc.doc_id) == doc.content_hash:
                continue
            total += await self.index_document(doc)
        # Remove documents whose files disappeared.
        for doc_id in set(existing) - seen:
            await self.store.delete_doc(doc_id)
            log.info("index.removed_missing", doc_id=doc_id)
        return total

    async def index_document(self, doc: Document) -> int:
        await self.store.delete_doc(doc.doc_id)
        chunks = self.chunker.chunk(doc)
        if not chunks:
            return 0
        # Stamp content hash onto meta so the store can detect changes later.
        for c in chunks:
            if c.meta:
                c.meta.extra["content_hash"] = doc.content_hash
        if self.summarize and self.reasoner:
            for c in chunks:
                c.summary = await self._summary(c.text)
        embeddings = await self.embedder.embed([c.text for c in chunks])
        for c, emb in zip(chunks, embeddings, strict=False):
            c.embedding = emb
        await self.store.upsert(chunks)
        if isinstance(self.store, InMemoryVectorStore):
            self.store.register_doc_hash(doc.doc_id, doc.content_hash)
        log.info("index.doc", title=doc.meta.title, chunks=len(chunks))
        return len(chunks)

    async def index_path(self, path: str) -> int:
        doc = load_document(path)
        return await self.index_document(doc) if doc else 0

    async def remove_path(self, path: str) -> None:
        doc = Document(meta=_stub_meta(path), text="x")  # doc_id derives from path
        await self.store.delete_doc(doc.doc_id)

    async def _summary(self, text: str) -> str | None:
        try:
            msg = [
                ChatMessage("system", "Summarize the passage in one factual sentence."),
                ChatMessage("user", text[:1500]),
            ]
            return (await self.reasoner.complete(msg, temperature=0.0, max_tokens=80)).text
        except Exception:  # noqa: BLE001
            return None


def _stub_meta(path: str):
    from .models import DocMeta

    return DocMeta(title="", source_file=path)


async def build_store(config: Config) -> VectorStore:
    """Pick pgvector if a DATABASE_URL is set, else the in-memory fallback."""
    secrets = get_secrets()
    dims = int(config.get("llm.embeddings.dimensions", 1024))
    if secrets.database_url:
        try:
            store = await PgVectorStore.create(secrets.database_url, dims)
            log.info("store.pgvector", dimensions=dims)
            return store
        except Exception as exc:  # noqa: BLE001
            log.error("store.pgvector_failed", error=str(exc))
    log.warning("store.in_memory", reason="no/failed DATABASE_URL — dev fallback")
    return InMemoryVectorStore()


__all__ = ["Indexer", "build_store", "SUPPORTED"]
