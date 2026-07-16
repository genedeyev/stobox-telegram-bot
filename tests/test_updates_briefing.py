"""Proactive 'What's new at Stobox' updates briefing + migration status line."""

from __future__ import annotations

from datetime import date

import pytest

from stobox_ai.channels.telegram import proactive as pro
from stobox_ai.channels.telegram.proactive import migration_status_line
from stobox_ai.market import MarketSnapshot

MIGRATION = {
    "burn_window_opens": "2026-07-20",
    "burn_deadline": "2026-09-15T23:59:00Z",
    "claim_opens": "2026-09-16",
}


class _Canon:
    def __init__(self, m):
        self._m = m

    def get(self, path, default=None):
        return self._m if path == "tokens.stbu.migration" else default


# --------------------------------------------------------------------------- #
# migration_status_line — one line per phase, grounded in canonical dates
# --------------------------------------------------------------------------- #
def test_migration_line_before_window_counts_down_to_open():
    line = migration_status_line(_Canon(MIGRATION), date(2026, 7, 16))
    assert "opens" in line and "in 4 days" in line and "20 Jul 2026" in line


def test_migration_line_open_counts_down_to_deadline():
    line = migration_status_line(_Canon(MIGRATION), date(2026, 8, 1))
    assert "OPEN" in line and "burn deadline" in line and "15 Sep 2026" in line


def test_migration_line_deadline_day_is_today():
    line = migration_status_line(_Canon(MIGRATION), date(2026, 9, 15))
    assert "OPEN" in line and "today" in line


def test_migration_line_claims_open():
    line = migration_status_line(_Canon(MIGRATION), date(2026, 9, 16))
    assert "claims are open" in line.lower()


def test_migration_line_window_closed_before_claims():
    m = {**MIGRATION, "claim_opens": "2026-09-20"}
    line = migration_status_line(_Canon(m), date(2026, 9, 17))
    assert "closed" in line.lower() and "20 Sep 2026" in line


def test_migration_line_none_without_deadline():
    assert migration_status_line(_Canon({}), date(2026, 7, 16)) is None
    assert migration_status_line(None, date(2026, 7, 16)) is None


# --------------------------------------------------------------------------- #
# _build_updates_briefing — composes the enabled sources
# --------------------------------------------------------------------------- #
def _snapshot():
    return MarketSnapshot(
        price_usd=0.00104287, market_cap_usd=130358.9, volume_24h_usd=63.68,
        change_24h_pct=-3.98, source="CoinGecko", as_of="2026-07-16 09:29 UTC",
    )


class _Sub:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


class _Cfg:
    def __init__(self, updates):
        self._updates = updates

    def section(self, path):
        return _Sub(self._updates)

    def get(self, k, default=None):
        return default


class _Assembler:
    def __init__(self, canon):
        self.canonicals = canon


class _Engine:
    def __init__(self, *, canon=None, snap=None, posts=None, updates=None):
        self.assembler = _Assembler(canon) if canon is not None else None
        self._snap = snap
        self.blog_posts = posts or []
        self.config = _Cfg(updates if updates is not None else {})

    async def market_snapshot(self):
        return self._snap


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, **kw):
        self.sent.append((chat, text))


class _Ctx:
    def __init__(self):
        self.bot = _Bot()


@pytest.mark.asyncio
async def test_briefing_composes_all_three_blocks():
    eng = _Engine(
        canon=_Canon(MIGRATION), snap=_snapshot(),
        posts=[{"title": "Tokenizing Real Estate", "url": "https://stobox.io/blog/re"}],
    )
    sched = pro.ProactiveScheduler(eng, app=None)
    # Pin "today" so the migration phase is deterministic.
    text = await _build(sched, eng, date(2026, 8, 1))
    assert "What's new at Stobox" in text
    assert "migration is OPEN" in text.lower() or "OPEN" in text          # migration
    assert "STBU" in text and "not advice" in text                        # market
    assert "Tokenizing Real Estate" in text and "stobox.io/blog/re" in text  # blog
    assert "staff never DM you first" in text                             # footer


async def _build(sched, eng, today):
    """Call _build_updates_briefing with a pinned date."""
    import stobox_ai.channels.telegram.proactive as _p

    class _D:
        @staticmethod
        def now(tz=None):
            class _T:
                @staticmethod
                def date():
                    return today
            return _T()
    orig = _p.datetime
    _p.datetime = _D
    try:
        return await sched._build_updates_briefing()
    finally:
        _p.datetime = orig


@pytest.mark.asyncio
async def test_briefing_respects_toggles():
    eng = _Engine(
        canon=_Canon(MIGRATION), snap=_snapshot(),
        posts=[{"title": "Post", "url": "https://s/b"}],
        updates={"include_market": False, "include_blog": False},
    )
    sched = pro.ProactiveScheduler(eng, app=None)
    text = await _build(sched, eng, date(2026, 8, 1))
    assert "OPEN" in text                       # migration kept
    assert "not advice" not in text             # market suppressed
    assert "https://s/b" not in text            # blog suppressed


@pytest.mark.asyncio
async def test_briefing_drops_migration_when_countdown_already_posted():
    """No double migration post: if the public countdown already fired today, the
    briefing omits its migration block (but still carries market + blog)."""
    eng = _Engine(canon=_Canon(MIGRATION), snap=_snapshot(),
                  posts=[{"title": "Post", "url": "https://s/b"}])
    sched = pro.ProactiveScheduler(eng, app=None)
    sched._countdown_last = "2026-08-01"          # countdown posted today
    text = await _build(sched, eng, date(2026, 8, 1))
    assert text is not None
    assert "OPEN" not in text and "burn deadline" not in text   # migration suppressed
    assert "not advice" in text and "https://s/b" in text       # market + blog remain


@pytest.mark.asyncio
async def test_briefing_none_when_nothing_resolves():
    eng = _Engine(canon=_Canon({}), snap=None, posts=[])
    sched = pro.ProactiveScheduler(eng, app=None)
    assert await _build(sched, eng, date(2026, 8, 1)) is None


@pytest.mark.asyncio
async def test_job_skips_duplicate_second_slot():
    eng = _Engine(canon=_Canon(MIGRATION), snap=_snapshot(),
                  posts=[{"title": "P", "url": "https://s/b"}])
    sched = pro.ProactiveScheduler(eng, app=None)
    sched._known_chats = lambda: {"c1", "c2"}
    sched._in_quiet_hours = lambda: False
    ctx = _Ctx()
    await sched._updates_briefing_job(ctx)
    first = len(ctx.bot.sent)
    assert first == 2                            # posted to both chats
    await sched._updates_briefing_job(ctx)       # identical content → skipped
    assert len(ctx.bot.sent) == first


@pytest.mark.asyncio
async def test_job_silent_when_no_chats():
    eng = _Engine(canon=_Canon(MIGRATION), snap=_snapshot(), posts=[])
    sched = pro.ProactiveScheduler(eng, app=None)
    sched._known_chats = lambda: set()
    sched._in_quiet_hours = lambda: False
    ctx = _Ctx()
    await sched._updates_briefing_job(ctx)
    assert ctx.bot.sent == []
