"""Quiet-time blog sharing: rotation without repeats (offline, mocked)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stobox_ai.channels.telegram import proactive as pro


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, **kw):
        self.sent.append((chat, text))


class _Ctx:
    def __init__(self):
        self.bot = _Bot()


class _Engine:
    def __init__(self, posts):
        self._posts = posts

    def all_blog_posts(self):
        return list(self._posts)


@pytest.mark.asyncio
async def test_blog_rotation_no_repeat_until_exhausted(monkeypatch):
    async def fake_og(url, timeout=15.0):
        return {}
    monkeypatch.setattr(pro, "fetch_og_meta", fake_og)

    posts = [{"url": f"https://stobox.io/blog/p{i}", "title": f"Post {i}"} for i in range(3)]
    sched = pro.ProactiveScheduler(_Engine(posts), app=None)
    ctx = _Ctx()

    for _ in range(3):
        assert await sched._share_blog(ctx, "c1") is True
    shared = {url for _, text in ctx.bot.sent for url in [t for t in [p["url"] for p in posts] if t in text]}
    assert len(shared) == 3          # all three distinct posts, no repeat

    # A 4th share resets the rotation and posts again (doesn't get stuck).
    assert await sched._share_blog(ctx, "c1") is True
    assert len(ctx.bot.sent) == 4


@pytest.mark.asyncio
async def test_no_posts_means_no_blog_share(monkeypatch):
    sched = pro.ProactiveScheduler(_Engine([]), app=None)
    assert await sched._share_blog(_Ctx(), "c1") is False


@pytest.mark.asyncio
async def test_blog_share_includes_link_and_title(monkeypatch):
    async def fake_og(url, timeout=15.0):
        return {"title": "Tokenizing Real Estate", "description": "A practical guide."}
    monkeypatch.setattr(pro, "fetch_og_meta", fake_og)
    posts = [{"url": "https://stobox.io/blog/re", "title": "fallback"}]
    sched = pro.ProactiveScheduler(_Engine(posts), app=None)
    ctx = _Ctx()
    await sched._share_blog(ctx, "c1")
    _, text = ctx.bot.sent[0]
    assert "Tokenizing Real Estate" in text and "https://stobox.io/blog/re" in text
    assert text.count("http") == 1                   # link discipline: one link


class _Mem:
    def __init__(self):
        self.t = datetime(2026, 7, 15, tzinfo=UTC)
        self.last: dict[str, datetime] = {}

    def last_activity(self, tk):
        return self.last.get(tk)

    def add_turn(self, tk, role, text):
        self.t += timedelta(minutes=1)
        self.last[tk] = self.t


class _Cfg:
    def get(self, k, d=None):
        return {"proactive.growth.inactivity_minutes": 0,
                "proactive.growth.max_unanswered_revivals": 2}.get(k, d)


class _RevivalEngine:
    def __init__(self, posts):
        self._posts = posts
        self.memory = _Mem()
        self.config = _Cfg()

    def all_blog_posts(self):
        return list(self._posts)


@pytest.mark.asyncio
async def test_revival_backs_off_then_resumes_after_human(monkeypatch):
    async def fake_og(url, timeout=15.0):
        return {}
    monkeypatch.setattr(pro, "fetch_og_meta", fake_og)

    eng = _RevivalEngine([{"url": "https://stobox.io/blog/a", "title": "A"}])
    sched = pro.ProactiveScheduler(eng, app=None)
    sched._known_chats = lambda: {"c1"}
    sched._in_quiet_hours = lambda: False
    ctx = _Ctx()

    # max_unanswered=2 → nudges on cycles 1 & 2, then dormant on cycle 3.
    await sched._revival_job(ctx)
    await sched._revival_job(ctx)
    await sched._revival_job(ctx)
    assert len(ctx.bot.sent) == 2                     # backed off, didn't spam

    # A human speaks → streak resets → Stoby engages again.
    eng.memory.add_turn("telegram:c1:main", "user", "hi")
    await sched._revival_job(ctx)
    assert len(ctx.bot.sent) == 3


@pytest.mark.asyncio
async def test_prompt_rotation_no_repeat():
    sched = pro.ProactiveScheduler(_Engine([]), app=None)
    ctx = _Ctx()
    n = len(pro._ENGAGE_PROMPTS)
    for _ in range(n):
        assert await sched._share_prompt(ctx, "c1") is True
    sent = [t for _, t in ctx.bot.sent]
    assert len(set(sent)) == n              # every prompt distinct before repeating
    assert await sched._share_prompt(ctx, "c1") is True   # resets, keeps going


@pytest.mark.asyncio
async def test_revival_content_alternates_blog_and_prompt(monkeypatch):
    async def fake_og(url, timeout=15.0):
        return {}
    monkeypatch.setattr(pro, "fetch_og_meta", fake_og)
    sched = pro.ProactiveScheduler(_Engine([{"url": "https://s/b", "title": "B"}]), app=None)
    ctx = _Ctx()
    await sched._revival_content(ctx, "c1")     # i=0 → blog first
    await sched._revival_content(ctx, "c1")     # i=1 → prompt first
    texts = [t for _, t in ctx.bot.sent]
    assert any("https://s/b" in t for t in texts)                 # a blog went out
    assert any(t in pro._ENGAGE_PROMPTS for t in texts)           # a prompt went out
