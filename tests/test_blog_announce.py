"""New-blog announcement logic (offline)."""

from __future__ import annotations

import pytest

from stobox_ai.core.engine import AgentEngine


async def _engine_with_blog(config, urls: dict[str, str]) -> AgentEngine:
    eng = await AgentEngine.create(config)
    eng._blog_index = dict(urls)
    return eng


@pytest.mark.asyncio
async def test_blog_diff_baseline_then_new(config):
    eng = await _engine_with_blog(config, {
        "https://www.stobox.io/blog/old-post": "Old Post",
    })
    # First call = baseline → nothing announced (restart never spams).
    assert eng.pop_new_blog_posts() == []
    # Same index → still nothing.
    assert eng.pop_new_blog_posts() == []
    # New post appears after a sync.
    eng._blog_index["https://www.stobox.io/blog/new-post"] = "New Post"
    new = eng.pop_new_blog_posts()
    assert new == [{"url": "https://www.stobox.io/blog/new-post", "title": "New Post"}]
    # Not marked yet → keeps returning until delivery succeeds (retry-safe).
    assert eng.pop_new_blog_posts() == new
    eng.mark_blog_announced(new[0]["url"])
    assert eng.pop_new_blog_posts() == []


@pytest.mark.asyncio
async def test_blog_diff_waits_for_first_sync(config):
    """An empty index (boot sync still running) must NOT be baselined."""
    eng = await _engine_with_blog(config, {})
    assert eng.pop_new_blog_posts() == []          # no baseline taken
    # Sync completes and finds existing posts → these are the baseline, not news.
    eng._blog_index = {"https://www.stobox.io/blog/existing": "Existing"}
    assert eng.pop_new_blog_posts() == []          # baselined now
    eng._blog_index["https://www.stobox.io/blog/brand-new"] = "Brand New"
    assert len(eng.pop_new_blog_posts()) == 1      # only the truly new one


@pytest.mark.asyncio
async def test_share_nudge_every_fourth_helpful_answer(config):
    from stobox_ai.core.types import Author, ChatType, IncomingMessage

    eng = await AgentEngine.create(config)

    def m(i):
        return IncomingMessage(author=Author(external_id="sharer"), text="What is the STBU token?",
            chat_id="sh", chat_type=ChatType.PRIVATE, message_id=str(i), raw={"addressed": True})

    nudges = []
    for i in range(8):
        r = await eng.handle(m(i))
        nudges.append(bool(r.meta.get("share_nudge")))
    # Exactly on the 4th and 8th helpful answers.
    assert nudges == [False, False, False, True, False, False, False, True]


@pytest.mark.asyncio
async def test_no_share_nudge_on_refusals(config):
    from stobox_ai.core.types import Author, ChatType, IncomingMessage

    eng = await AgentEngine.create(config)
    msg = IncomingMessage(author=Author(external_id="refuser"), text="should I buy STBU?",
        chat_id="rf", chat_type=ChatType.PRIVATE, message_id="1", raw={"addressed": True})
    for _ in range(8):
        r = await eng.handle(msg)
        assert not r.meta.get("share_nudge")   # intercepted advice answers never nudge


@pytest.mark.asyncio
async def test_fetch_og_meta_degrades_to_empty():
    from stobox_ai.channels.telegram.proactive import fetch_og_meta

    # Unreachable host → {} (announcement degrades to a link card, no crash).
    meta = await fetch_og_meta("http://127.0.0.1:1/nope", timeout=0.5)
    assert meta == {}
