"""Knowledge data model.

Every document carries the metadata the spec requires (title, version, author,
date, category, product, language, visibility, confidence). Every chunk stores
text, embedding, summary, keywords, references, source url/file, section,
revision.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class DocMeta:
    title: str
    source_file: str
    version: str | None = None
    author: str | None = None
    doc_date: date | None = None
    category: str | None = None          # e.g. tokenomics, legal, product, roadmap
    product: str | None = None           # e.g. Compass, STBU, Wallet
    language: str = "en"
    visibility: str = "public"           # public | internal | restricted
    confidence: float = 1.0              # source-level trust weight
    source_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Chunk:
    doc_id: str
    text: str
    section: str | None = None
    revision: int = 1
    summary: str | None = None
    keywords: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    ordinal: int = 0
    meta: DocMeta | None = None

    @property
    def chunk_id(self) -> str:
        h = hashlib.sha256(f"{self.doc_id}:{self.ordinal}:{self.text}".encode()).hexdigest()
        return h[:32]


@dataclass(slots=True)
class Document:
    meta: DocMeta
    text: str
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def doc_id(self) -> str:
        return hashlib.sha256(self.meta.source_file.encode()).hexdigest()[:32]


@dataclass(slots=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    vector_score: float = 0.0
    bm25_score: float = 0.0
    rerank_score: float | None = None
