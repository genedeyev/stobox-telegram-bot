"""Proactive engagement: HTML-safe broadcasts (no raw <b> tags) and the
wall-clock evangelist cadence that survives redeploys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from stobox_ai.channels.telegram.proactive import (
    ProactiveScheduler,
    send_with_flood_control,
)


class _Bot:
    def __init__(self, reject_html=False):
        self.reject_html = reject_html
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        from telegram.error import BadRequest

        if self.reject_html and kwargs.get("parse_mode") == "HTML":
            raise BadRequest("can't parse entities")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


async def test_html_send_uses_parse_mode():
    bot = _Bot()
    status = await send_with_flood_control(bot, 1, "<b>RWA</b> education", html=True)
    assert status == "ok"
    assert bot.sent[0]["parse_mode"] == "HTML"
    assert bot.sent[0]["text"] == "<b>RWA</b> education"   # tags preserved, rendered bold


async def test_html_send_falls_back_to_stripped_plain_text():
    bot = _Bot(reject_html=True)
    status = await send_with_flood_control(bot, 1, "<b>RWA</b> <h1>bad</h1>", html=True)
    assert status == "ok"
    # No raw tags reach the chat, and the post is NOT dropped.
    assert "parse_mode" not in bot.sent[0]
    assert "<b>" not in bot.sent[0]["text"] and "<h1>" not in bot.sent[0]["text"]
    assert "RWA" in bot.sent[0]["text"]


# --------------------------------------------------------------------------- #
# Evangelist cadence: wall-clock, not process uptime
# --------------------------------------------------------------------------- #

def _sched(tmp_path, last_iso=""):
    eng = SimpleNamespace(
        config=SimpleNamespace(get=lambda k, d=None: {
            "proactive.state_path": str(tmp_path / "p.json"),
            "proactive.evangelist.interval_hours": 3,
            "proactive.evangelist.quiet_hours": [0, 7],
        }.get(k, d)),
    )
    app = SimpleNamespace(bot_data={}, job_queue=None)
    s = ProactiveScheduler(eng, app)
    s._evangelist_last = last_iso
    s._in_quiet_hours = lambda: False
    s._known_chats = lambda: {"-100"}
    return s


async def test_evangelist_skips_when_recently_posted(tmp_path):
    """A redeploy re-fires the warmup timer; the gap guard must skip if we
    posted within the interval (else every deploy = a spam post)."""
    posted = []
    s = _sched(tmp_path, last_iso=(datetime.now(UTC) - timedelta(minutes=30)).isoformat())
    s._post_poll = lambda ctx: posted.append("poll")

    async def _fail(*a, **k):
        posted.append("post")

    s.engine.retriever = SimpleNamespace(retrieve=_fail)
    await s._evangelist_job(context=SimpleNamespace(bot=_Bot()))
    assert posted == []            # 30 min < 90% of 3h → skipped


async def test_evangelist_runs_when_gap_elapsed(tmp_path, monkeypatch):
    """After a full interval, the job proceeds past the gap guard into content
    generation (which we stub to fail — reasoner errors are caught by the job)."""
    import stobox_ai.channels.telegram.proactive as pro

    monkeypatch.setattr(pro.random, "choice", lambda seq: "RWA education")  # never Poll
    reached = []
    s = _sched(tmp_path, last_iso=(datetime.now(UTC) - timedelta(hours=4)).isoformat())

    async def _retrieve(q):
        reached.append("retrieved")
        return []

    async def _complete(*a, **k):
        reached.append("completed")
        raise RuntimeError("stop — past the guard is all we assert")

    s.engine.retriever = SimpleNamespace(retrieve=_retrieve)
    s.engine.reasoner = SimpleNamespace(complete=_complete)
    s.engine.system_messages = lambda: None
    s.engine.prompts = SimpleNamespace(render=lambda *a, **k: "prompt")
    await s._evangelist_job(context=SimpleNamespace(bot=_Bot()))
    assert "retrieved" in reached and "completed" in reached  # passed the gap guard


# --------------------------------------------------------------------------- #
# Daily/weekly jobs must be wall-clock (run_daily), not first=interval — else
# frequent redeploys reset the timer and they never fire (the "haven't seen the
# digest" bug).
# --------------------------------------------------------------------------- #

def test_parse_hhmm():
    from stobox_ai.channels.telegram.proactive import _parse_hhmm
    assert _parse_hhmm("08:00") == (8, 0)
    assert _parse_hhmm("17:30") == (17, 30)
    assert _parse_hhmm("bad") == (8, 0)              # default
    assert _parse_hhmm("25:99", default=(9, 0)) == (9, 0)
    assert _parse_hhmm("", default=(-1, -1)) == (-1, -1)


def test_digest_and_content_scheduled_daily_not_delayed(tmp_path):
    """The digest/content jobs are registered via run_daily (fixed UTC time),
    never run_repeating(first=interval) — the redeploy-reset bug."""
    from types import SimpleNamespace

    daily, repeating = [], []

    class _JQ:
        def run_daily(self, cb, time=None, **k):
            daily.append(cb.__name__)

        def run_repeating(self, cb, interval=None, first=None, **k):
            repeating.append((cb.__name__, first))

    eng = SimpleNamespace(config=SimpleNamespace(
        get=lambda k, d=None: {"proactive.state_path": str(tmp_path / "p.json")}.get(k, d),
        section=lambda k: SimpleNamespace(get=lambda kk, d=None: d)))
    app = SimpleNamespace(bot_data={}, job_queue=_JQ())
    ProactiveScheduler(eng, app).schedule()

    assert "_digest_job" in daily
    assert "_content_job" in daily
    # Neither is a delayed run_repeating.
    assert not any(name in ("_digest_job", "_content_job") for name, _ in repeating)
