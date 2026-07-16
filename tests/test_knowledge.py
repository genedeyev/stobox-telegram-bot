"""Chunking + retrieval + store tests (offline)."""

from __future__ import annotations

import pytest

from stobox_ai.knowledge.chunking import SemanticChunker
from stobox_ai.knowledge.ingest import load_directory, load_document
from stobox_ai.knowledge.models import DocMeta, Document
from stobox_ai.knowledge.store import InMemoryVectorStore
from stobox_ai.llm.local import LocalHashEmbeddings


def _doc() -> Document:
    text = (
        "# STBU Token\n\n"
        "STBU is the native utility token of the Stobox ecosystem.\n\n"
        "## Utility\n\n"
        "It is used to access premium features and settle certain fees.\n\n"
        "## Security\n\n"
        "Never share your seed phrase or private key with anyone.\n"
    )
    return Document(meta=DocMeta(title="STBU Token", source_file="stbu.md", version="2.1"), text=text)


def test_semantic_chunking_preserves_sections():
    chunks = SemanticChunker(target_tokens=40, max_tokens=80).chunk(_doc())
    assert chunks, "expected at least one chunk"
    sections = {c.section for c in chunks}
    assert any(s and "Utility" in s for s in sections)
    assert all(c.keywords for c in chunks)
    assert all(c.meta and c.meta.title == "STBU Token" for c in chunks)


def test_load_markdown_frontmatter(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("---\ntitle: Test Doc\nversion: '1.0'\ncategory: product\n---\nBody text here.")
    doc = load_document(p)
    assert doc is not None
    assert doc.meta.title == "Test Doc"
    assert doc.meta.version == "1.0"
    assert doc.meta.category == "product"


def test_load_seed_docs():
    docs = load_directory("docs")
    titles = {d.meta.title for d in docs}
    assert "STBU Token Overview" in titles


@pytest.mark.asyncio
async def test_in_memory_vector_search_ranks_relevant_chunk():
    store = InMemoryVectorStore()
    embedder = LocalHashEmbeddings(model="local-hash", dimensions=256)
    chunks = SemanticChunker(target_tokens=40, max_tokens=80).chunk(_doc())
    embeddings = await embedder.embed([c.text for c in chunks])
    for c, e in zip(chunks, embeddings, strict=False):
        c.embedding = e
    await store.upsert(chunks)

    q = await embedder.embed_one("seed phrase private key security")
    results = await store.search(q, top_k=3)
    assert results
    # The security chunk should surface for a security query.
    assert any("seed phrase" in c.text.lower() for c, _ in results)


@pytest.mark.asyncio
async def test_index_directory_does_not_prune_remote_docs(tmp_path):
    """Regression: sync_knowledge shares one store between the local-docs
    re-index and the web/github sources. index_directory's orphan cleanup must
    prune ONLY local docs — it used to delete the entire remote corpus right
    after it was indexed, pinning the index at the local-doc count."""
    from stobox_ai.knowledge.chunking import SemanticChunker
    from stobox_ai.knowledge.indexer import Indexer
    from stobox_ai.knowledge.models import DocMeta, Document
    from stobox_ai.llm.local import LocalHashEmbeddings

    store = InMemoryVectorStore()
    idx = Indexer(store, LocalHashEmbeddings("local", 128), SemanticChunker())

    # A local doc on disk…
    (tmp_path / "guide.md").write_text(
        "# Guide\n\n" + ("Stobox tokenizes real-world assets. " * 40))
    await idx.index_directory(str(tmp_path))
    local_count = await store.count()
    assert local_count > 0

    # …plus remote (web + github) docs indexed into the SAME store.
    for scheme, url in [("web", "https://www.stobox.io/blog/post-a"),
                        ("github", "StoboxTechnologies/repo/Token.sol")]:
        doc = Document(
            meta=DocMeta(title=f"{scheme} doc", source_file=f"{scheme}://{url}",
                         source_url=f"https://{url}"),
            text=("Deep grounded tokenization content for retrieval. " * 40))
        await idx.index_document(doc)
    with_remote = await store.count()
    assert with_remote > local_count

    # Re-index the directory again (as sync_knowledge does AFTER sync_sources).
    await idx.index_directory(str(tmp_path))
    assert await store.count() == with_remote, "remote docs must survive a local re-index"

    # And a genuinely-removed LOCAL file IS still pruned.
    (tmp_path / "guide.md").unlink()
    await idx.index_directory(str(tmp_path))
    remaining = {c.meta.source_file for c in await store.all_chunks()}
    assert all("://" in s for s in remaining), "local doc pruned, remote kept"
    assert len(remaining) == 2
