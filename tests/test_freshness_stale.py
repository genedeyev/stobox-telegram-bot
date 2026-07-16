"""Staleness honesty: a missed resync must not let Stoby present a day-old
corpus as fresh, and a failed resync must alert + retry instead of going silent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from stobox_ai.guardrails.canonicals import load_canonicals
from stobox_ai.guardrails.freshness import FreshnessBuilder


def _builder(last_sync):
    return FreshnessBuilder(canon=load_canonicals("canonicals.yaml"), last_sync=last_sync)


def test_fresh_sync_carries_no_stale_marker():
    text = _builder(datetime.now(UTC) - timedelta(hours=3)).build()
    assert "last synced" in text
    assert "STALE" not in text


def test_day_old_sync_is_marked_stale():
    text = _builder(datetime.now(UTC) - timedelta(hours=36)).build()
    assert "STALE: more than a day old" in text
    assert "hedge recency-sensitive answers" in text


async def test_failed_resync_alerts_admins_and_schedules_retry():
    from stobox_ai.channels.telegram.proactive import ProactiveScheduler

    class _Engine:
        config = None
        decisions = SimpleNamespace(prune=None)

        async def sync_knowledge(self):
            raise RuntimeError("crawler down")

    dms: list[str] = []

    class _Adapter:
        async def dm_admins(self, context, text, **kw):
            dms.append(text)
            return 1

    retries: list = []
    jq = SimpleNamespace(run_once=lambda cb, when: retries.append(when))
    app = SimpleNamespace(bot_data={"adapter": _Adapter()}, job_queue=jq)

    sched = ProactiveScheduler(_Engine(), app)

    async def _no_prune(days):
        return 0

    sched.engine.decisions.prune = _no_prune
    sched.engine.config = SimpleNamespace(get=lambda k, d=None: d)

    await sched._resync_job(context=SimpleNamespace(bot=None))
    assert retries == [1800]                       # retry scheduled in 30 min
    assert any("FAILED" in t for t in dms)         # admins alerted

    # Retries are bounded: after 3 failures it gives up loudly.
    await sched._resync_job(context=SimpleNamespace(bot=None))
    await sched._resync_job(context=SimpleNamespace(bot=None))
    await sched._resync_job(context=SimpleNamespace(bot=None))
    assert len(retries) == 3
    assert any("giving up" in t for t in dms)
