"""Remote knowledge sync — build sources from config, fetch, and index.

Runs the web crawler and GitHub ingester and feeds their Documents through the
same incremental indexer as local ``docs/`` (content-hash change detection, so
re-syncing only re-embeds what changed). Exposed via the ``stobox-sync`` CLI, the
``/sync`` admin command, and optionally on boot.
"""

from __future__ import annotations

import os

from ..config import Config
from ..logging import get_logger
from .indexer import Indexer
from .sources import (
    Fetcher,
    GitHubSource,
    HttpxFetcher,
    LlmsTxtSource,
    Source,
    WebSource,
)

log = get_logger(__name__)


def build_sources(config: Config) -> list[Source]:
    s = config.section("knowledge.sources")
    sources: list[Source] = []

    # llms.txt / llms-full.txt — the curated, canonical AI reference (highest value).
    llms = s.get("llms") or {}
    if llms.get("enabled"):
        sources.append(
            LlmsTxtSource(
                hosts=llms.get("hosts", []),
                confidence=float(llms.get("confidence", 1.0)),
            )
        )

    web = s.get("web") or {}
    if web.get("enabled"):
        sources.append(
            WebSource(
                seeds=web.get("seeds", []),
                allow_domains=web.get("allow_domains"),
                max_pages=int(web.get("max_pages", 200)),
                max_depth=int(web.get("max_depth", 3)),
                delay_seconds=float(web.get("delay_seconds", 0.3)),
                paginate=web.get("paginate") or [],
            )
        )

    gh = s.get("github") or {}
    if gh.get("enabled"):
        sources.append(
            GitHubSource(
                org=gh.get("org"),
                repos=gh.get("repos") or [],
                branch=gh.get("branch"),
                include_ext=gh.get("include_ext"),
                include_code=bool(gh.get("include_code", True)),
                max_files=int(gh.get("max_files", 500)),
                max_files_per_repo=int(gh.get("max_files_per_repo", 120)),
                token=os.environ.get("GITHUB_TOKEN") or None,
            )
        )
    return sources


async def sync_sources(
    indexer: Indexer, config: Config, fetcher: Fetcher | None = None
) -> dict[str, int]:
    """Fetch every configured source and index its Documents. Returns per-source
    chunk counts. Uses an injected fetcher when provided (tests)."""
    sources = build_sources(config)
    if not sources:
        log.info("sync.no_sources")
        return {}

    owns_fetcher = fetcher is None
    fetcher = fetcher or HttpxFetcher()
    results: dict[str, int] = {}
    try:
        # Content-hash gate, same as index_directory: without it every daily
        # resync deleted + re-chunked + RE-EMBEDDED the entire remote corpus
        # (~hundreds of unchanged docs of recurring embedding spend).
        existing = await indexer.store.doc_hashes()
        for source in sources:
            try:
                documents = await source.fetch(fetcher)
            except Exception as exc:  # noqa: BLE001 - one source failing shouldn't kill others
                log.error("sync.source_failed", source=source.name, error=str(exc))
                results[source.name] = 0
                continue
            chunks = skipped = 0
            for doc in documents:
                if existing.get(doc.doc_id) == doc.content_hash:
                    skipped += 1
                    continue
                chunks += await indexer.index_document(doc)
            results[source.name] = chunks
            log.info("sync.indexed", source=source.name, docs=len(documents),
                     chunks=chunks, unchanged_skipped=skipped)
    finally:
        if owns_fetcher:
            await fetcher.aclose()
    return results


def cli() -> None:
    """``stobox-sync`` entrypoint: crawl stobox.io + ingest the GitHub repos."""
    import argparse
    import asyncio

    from ..config import load_config
    from ..logging import configure_logging

    parser = argparse.ArgumentParser(description="Sync remote Stobox knowledge (web + GitHub).")
    parser.add_argument("--only", choices=["web", "github"], help="run just one source")
    args = parser.parse_args()

    configure_logging()
    config = load_config()

    async def run() -> None:
        indexer = await Indexer.create(config)
        if args.only:  # temporarily disable the other source
            other = "github" if args.only == "web" else "web"
            section = config.section("knowledge.sources").raw
            if other in section:
                section[other]["enabled"] = False
        results = await sync_sources(indexer, config)
        total = sum(results.values())
        log.info("sync.done", results=results, total_chunks=total)
        print(f"Synced: {results} (total {total} chunks)")

    asyncio.run(run())
