"""Blog announcements (Gene, 28.09.2026): each new post once per chat; a chat
that refuses (bot not yet admin of the channel) is retried, the others are not
held back; the first run records the feed without posting."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from stoby.announce import Announcer, caption, parse_rss  # noqa: E402

COMMUNITY, CHANNEL = -1001438480522, -1001349044622


def feed(*slugs: str) -> str:
    items = "".join(
        f"<item><title>Post {s} &amp; more</title><link>https://www.stobox.io/blog/{s}</link>"
        f"<description>About {s}.</description><category>Tech</category></item>" for s in slugs)
    return f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'


class Bot:
    def __init__(self, refuse: set[int] = frozenset()) -> None:
        self.sent: list[tuple[int, str]] = []
        self.refuse = set(refuse)

    async def send_photo(self, chat, photo, caption, parse_mode):
        if chat in self.refuse:
            raise RuntimeError("Forbidden: bot is not a member of the channel chat")
        self.sent.append((chat, caption))

    send_message = None


def client(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rss.xml":
            return httpx.Response(200, text=state["feed"])
        return httpx.Response(200, text='<meta property="og:image" content="https://www.stobox.io/og.png">')
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_first_run_is_a_baseline_then_new_posts_go_out_once(tmp_path):
    st = {"feed": feed("a", "b")}
    bot = Bot()
    a = Announcer(bot, [COMMUNITY, CHANNEL], tmp_path, "https://www.stobox.io/rss.xml", client(st))
    assert await a.tick() == 0 and bot.sent == []
    st["feed"] = feed("c", "a", "b")
    assert await a.tick() == 2
    assert {c for c, _ in bot.sent} == {COMMUNITY, CHANNEL}
    assert "Post c &amp; more" in bot.sent[0][1] and "stobox.io/blog/c" in bot.sent[0][1]
    assert await a.tick() == 0                     # no second copy


async def test_refusing_channel_does_not_block_chat_and_is_retried(tmp_path):
    st = {"feed": feed("a")}
    bot = Bot(refuse={CHANNEL})
    a = Announcer(bot, [COMMUNITY, CHANNEL], tmp_path, "https://www.stobox.io/rss.xml", client(st))
    await a.tick()
    st["feed"] = feed("new", "a")
    assert await a.tick() == 1 and bot.sent[0][0] == COMMUNITY
    bot.refuse.clear()                             # Gene adds the bot as channel admin
    assert await a.tick() == 1 and bot.sent[-1][0] == CHANNEL
    assert await a.tick() == 0


def test_parse_and_caption():
    posts = parse_rss(feed("x"))
    assert posts[0].url == "https://www.stobox.io/blog/x" and posts[0].title == "Post x & more"
    c = caption(posts[0])
    assert c.startswith("📰 <b>New on the Stobox blog</b>") and "Read the article" in c
    assert "—" not in c
