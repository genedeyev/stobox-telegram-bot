"""Quiet-time blog sharing: rotation without repeats (offline, mocked)."""

from __future__ import annotations

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
