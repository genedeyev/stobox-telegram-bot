"""Entrypoint: boot the engine, start the Telegram channel + docs watcher.

    python -m stobox_ai        # or the `stobox-bot` console script

Graceful: SIGINT/SIGTERM stop polling, the watcher, and flush cleanly.
"""

from __future__ import annotations

import asyncio
import signal

from .config import load_config
from .core.engine import AgentEngine
from .knowledge.watcher import DocsWatcher
from .logging import configure_logging, get_logger

log = get_logger("stobox_ai.main")


async def run() -> None:
    from .preflight import load_dotenv_if_present, run_preflight

    load_dotenv_if_present()
    pf = run_preflight()
    print(pf.render())
    if not pf.ready:
        print("\nStartup aborted — resolve the blockers above. See SETUP.md.")
        return

    config = load_config()
    configure_logging(config.get("app.log_level"))
    log.info("boot", app=config.get("app.name"), env=config.get("app.environment"))

    engine = await AgentEngine.create(config)
    log.info("index.ready", chunks=await engine.retriever.store.count(),
             synced=engine.last_sync.isoformat() if engine.last_sync else None)

    # Hot re-index on documentation changes.
    watcher: DocsWatcher | None = None
    if config.get("knowledge.watch", True):
        watcher = DocsWatcher(engine.indexer, config.get("knowledge.docs_path", "docs"))
        try:
            watcher.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("watcher.start_failed", error=str(exc))
            watcher = None

    from .channels.telegram import TelegramChannel

    channel = TelegramChannel(engine)
    await channel.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    log.info("ready")
    await stop.wait()

    log.info("shutdown")
    if watcher:
        watcher.stop()
    await channel.stop()


def main() -> None:
    configure_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover
        pass


if __name__ == "__main__":
    main()
