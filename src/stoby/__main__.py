"""Boot Stoby: refuse root, wire the sources, start polling."""

from __future__ import annotations

import asyncio
import os
import sys

from aiogram import Bot

from . import __version__
from .announce import Announcer
from .answer import Pipeline
from .config import Settings
from .leads import Leads
from .ledger import SpendLedger, log
from .llm import Answerer
from .sources.chain import ChainReader
from .sources.sig import SigClient
from .sources.site import SiteSource
from .telegram import StobyBot, heartbeat, run


def main() -> None:
    if hasattr(os, "getuid") and os.getuid() == 0 and not os.environ.get("STOBY_ALLOW_ROOT"):
        log.error("main.refuse_root", hint="start via /entrypoint.py, which drops root")
        raise SystemExit(78)
    s = Settings.from_env()
    if not s.telegram_token:
        log.error("main.no_token", hint="set TELEGRAM_BOT_TOKEN")
        raise SystemExit(64)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("main.no_model_key", hint="set ANTHROPIC_API_KEY")
        raise SystemExit(64)
    s.state_dir.mkdir(parents=True, exist_ok=True)
    site = SiteSource(s.site_url, s.state_dir, s.site_cache_minutes, s.site_stale_hours)
    sig = SigClient(s.sig_url, s.sig_token, s.sig_timeout_s)
    model = Answerer(s.answer_model, s.answer_effort)
    ledger = SpendLedger(s.state_dir, s.daily_cap_usd)
    pipeline = Pipeline(site, sig, model, ledger)
    chain = ChainReader({"Base": s.base_rpc}, s.rpc_timeout_s)
    leads = Leads(s.state_dir, s.crm_webhook_url, s.crm_webhook_secret, s.crm_daily_cap)
    bot = Bot(s.telegram_token)
    stoby = StobyBot(bot, pipeline, site, chain, leads, s.admin_ids)
    log.info("boot", version=__version__, commit=os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7],
             model=s.answer_model, sig=s.sig_url, site=s.site_url,
             sig_token=bool(s.sig_token), crm=bool(s.crm_webhook_url),
             announce_chats=list(s.announce_chats))

    async def _run() -> None:
        hb = asyncio.create_task(heartbeat(os.environ.get("HEARTBEAT_FILE", "/tmp/stobox-heartbeat")))
        tasks = [hb]
        if s.announce_chats:
            tasks.append(asyncio.create_task(Announcer(bot, list(s.announce_chats), s.state_dir).run()))
        try:
            await run(bot, stoby)
        finally:
            for t in tasks:
                t.cancel()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(0)


if __name__ == "__main__":
    main()
