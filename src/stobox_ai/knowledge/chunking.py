"""Semantic chunking.

Not fixed-size windows. We split on document structure first (Markdown/section
headings), then pack semantically-coherent paragraph/sentence groups up to a
token budget, with a small overlap to preserve context across boundaries.

Each chunk keeps its section path (e.g. "4 › Migration steps") so citations can
say "Section 4". Keyword extraction is a lightweight TF heuristic; the optional
LLM summary is added later by the indexer to avoid a hard dependency here.
"""

from __future__ import annotations

import re
from collections import Counter

from .models import Chunk, Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]{2,}")
_STOP = {
    "the", "and", "for", "are", "with", "that", "this", "you", "your", "from",
    "have", "has", "can", "will", "not", "but", "all", "any", "our", "more",
    "which", "their", "they", "them", "then", "than", "into", "when", "what",
}


def _count_tokens(text: str) -> int:
    """Approximate token count. Uses tiktoken when available, else a heuristic."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


def _keywords(text: str, k: int = 8) -> list[str]:
    words = [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP]
    return [w for w, _ in Counter(words).most_common(k)]


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Return (section_path, body) segments split on Markdown headings."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text)]
    segments: list[tuple[str, str]] = []
    # Preamble before the first heading.
    if matches[0].start() > 0:
        pre = text[: matches[0].start()].strip()
        if pre:
            segments.append(("", pre))
    stack: list[tuple[int, str]] = []  # (level, title)
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = " › ".join(t for _, t in stack)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            segments.append((path, body))
    return segments


class SemanticChunker:
    def __init__(self, target_tokens: int = 400, max_tokens: int = 800, overlap_tokens: int = 60):
        self.target = target_tokens
        self.max = max_tokens
        self.overlap = overlap_tokens

    def chunk(self, doc: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        ordinal = 0
        for section, body in _split_sections(doc.text):
            for text in self._pack(body):
                chunks.append(
                    Chunk(
                        doc_id=doc.doc_id,
                        text=text,
                        section=section or None,
                        ordinal=ordinal,
                        keywords=_keywords(text),
                        meta=doc.meta,
                    )
                )
                ordinal += 1
        return chunks

    def _pack(self, body: str) -> list[str]:
        """Pack paragraphs/sentences into ~target-token chunks with overlap."""
        units = self._units(body)
        out: list[str] = []
        cur: list[str] = []
        cur_tok = 0
        for unit in units:
            ut = _count_tokens(unit)
            if ut > self.max:  # a single huge unit -> hard split by sentences
                for piece in self._hard_split(unit):
                    out.append(piece)
                continue
            if cur_tok + ut > self.target and cur:
                out.append(" ".join(cur))
                cur, cur_tok = self._carry_overlap(cur)
            cur.append(unit)
            cur_tok += ut
        if cur:
            out.append(" ".join(cur))
        return [c.strip() for c in out if c.strip()]

    @staticmethod
    def _units(body: str) -> list[str]:
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        return paras or [body]

    def _carry_overlap(self, cur: list[str]) -> tuple[list[str], int]:
        """Seed the next chunk with the tail of the current one for continuity."""
        carried: list[str] = []
        tok = 0
        for unit in reversed(cur):
            ut = _count_tokens(unit)
            if tok + ut > self.overlap:
                break
            carried.insert(0, unit)
            tok += ut
        return carried, tok

    def _hard_split(self, text: str) -> list[str]:
        sents = _SENT_SPLIT.split(text)
        out: list[str] = []
        cur: list[str] = []
        tok = 0
        for s in sents:
            st = _count_tokens(s)
            if tok + st > self.target and cur:
                out.append(" ".join(cur))
                cur, tok = [], 0
            cur.append(s)
            tok += st
        if cur:
            out.append(" ".join(cur))
        return out
