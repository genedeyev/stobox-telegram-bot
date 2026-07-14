"""Filesystem watcher — hot re-index on documentation changes (no restart).

watchdog runs on a background thread; changes are debounced and marshalled onto
the asyncio loop where the Indexer runs. Satisfies the spec: "Watch filesystem.
Whenever docs change: re-parse, re-chunk, re-embed, re-index, without restart."
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..logging import get_logger
from .indexer import SUPPORTED, Indexer

log = get_logger(__name__)


class DocsWatcher:
    def __init__(self, indexer: Indexer, docs_path: str, debounce_seconds: float = 2.0) -> None:
        self.indexer = indexer
        self.docs_path = docs_path
        self.debounce = debounce_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer = None
        self._pending: set[str] = set()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        self._loop = asyncio.get_event_loop()
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:
                if event.is_directory:
                    return
                if Path(event.src_path).suffix.lower() in SUPPORTED:
                    watcher._enqueue(event.src_path, deleted=event.event_type == "deleted")

        self._observer = Observer()
        self._observer.schedule(Handler(), self.docs_path, recursive=True)
        self._observer.start()
        log.info("watcher.started", path=self.docs_path)

    def _enqueue(self, path: str, deleted: bool) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._schedule, path, deleted)

    def _schedule(self, path: str, deleted: bool) -> None:
        self._pending.add(f"{'-' if deleted else '+'}{path}")
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        await asyncio.sleep(self.debounce)
        pending, self._pending = self._pending, set()
        for item in pending:
            deleted, path = item[0] == "-", item[1:]
            try:
                if deleted:
                    await self.indexer.remove_path(path)
                    log.info("watcher.reindex_delete", path=path)
                else:
                    n = await self.indexer.index_path(path)
                    log.info("watcher.reindex", path=path, chunks=n)
            except Exception as exc:  # noqa: BLE001
                log.error("watcher.reindex_failed", path=path, error=str(exc))

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            log.info("watcher.stopped")
