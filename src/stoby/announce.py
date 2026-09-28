"""New blog posts → the community chat and the announcements channel.

Gene, 28.09.2026: every article published on the Stobox blog is posted in the
Announcements channel and in the community chat. Source: the site's own RSS
feed (https://www.stobox.io/rss.xml). The first run only records what is
already published (no flood of old posts); after that each new item is posted
once per chat, with its cover image when the page has one.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import httpx

from .ledger import log

RSS_URL = "https://www.stobox.io/rss.xml"
ALL = "*"          # baseline marker: published before the announcer started
_OG_IMAGE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)


@dataclass(frozen=True)
class Post:
    url: str
    title: str
    summary: str
    category: str


def parse_rss(xml_text: str) -> list[Post]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        url = (item.findtext("link") or "").strip()
        if not url.startswith("https://www.stobox.io/"):
            continue
        out.append(Post(url=url, title=html.unescape(item.findtext("title") or "").strip(),
                        summary=html.unescape(item.findtext("description") or "").strip(),
                        category=html.unescape(item.findtext("category") or "").strip()))
    return out


def caption(post: Post) -> str:
    summary = post.summary if len(post.summary) <= 600 else post.summary[:597].rsplit(" ", 1)[0] + "…"
    head = "📰 <b>New on the Stobox blog</b>" + (f" · {html.escape(post.category)}" if post.category else "")
    return (f"{head}\n\n<b>{html.escape(post.title)}</b>\n\n{html.escape(summary)}\n\n"
            f"👉 <a href=\"{html.escape(post.url, quote=True)}\">Read the article</a>")


async def og_image(url: str, client: httpx.AsyncClient | None = None) -> str | None:
    """The page's og:image (every stobox.io page and post has one)."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=True)
    try:
        r = await client.get(url)
        m = _OG_IMAGE.search(r.text[:60000])
        return m.group(1) if m else None
    except httpx.HTTPError:
        return None
    finally:
        if own:
            await client.aclose()


class Announcer:
    def __init__(self, bot, chats: list, state_dir: Path, rss_url: str = RSS_URL,
                 client: httpx.AsyncClient | None = None, per_tick: int = 3) -> None:
        self.bot, self.chats, self.rss_url, self.client = bot, chats, rss_url, client
        self.path = state_dir / "announced.json"
        self.per_tick = per_tick

    def _state(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None

    def _save(self, st: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            tmp.replace(self.path)
        except OSError as exc:
            log.error("announce.state_write_failed", error=str(exc))

    async def _get(self, url: str) -> httpx.Response:
        client = self.client or httpx.AsyncClient(timeout=15, follow_redirects=True)
        try:
            return await client.get(url)
        finally:
            if self.client is None:
                await client.aclose()

    async def _image(self, url: str) -> str | None:
        try:
            r = await self._get(url)
            m = _OG_IMAGE.search(r.text[:60000])
            return m.group(1) if m else None
        except httpx.HTTPError:
            return None

    async def tick(self) -> int:
        """Post new items; returns how many chat posts went out."""
        try:
            r = await self._get(self.rss_url)
            r.raise_for_status()
            posts = parse_rss(r.text)
        except (httpx.HTTPError, ET.ParseError) as exc:
            log.warning("announce.feed_unreadable", error=type(exc).__name__)
            return 0
        st = self._state()
        if st is None:                          # first run: remember, don't post
            self._save({"seen": {p.url: [ALL] for p in posts}})
            log.info("announce.baseline", posts=len(posts))
            return 0
        seen: dict[str, list] = st.get("seen", {})
        sent = 0
        def pending(url: str) -> set[str]:
            done = set(map(str, seen.get(url, [])))
            return set() if ALL in done else set(map(str, self.chats)) - done

        fresh = [p for p in reversed(posts) if pending(p.url)]      # oldest new first
        # One line per check, so "is the announcer alive and reading the feed?"
        # is answered by the log (acceptance reads it), not by silence.
        log.info("announce.checked", posts=len(posts), new=len(fresh))
        for post in fresh[: self.per_tick]:
            done = set(map(str, seen.get(post.url, [])))
            img = await self._image(post.url)
            text = caption(post)
            for chat in self.chats:
                if str(chat) in done:
                    continue
                chat_id, _, topic = str(chat).partition(":")
                where = {"message_thread_id": int(topic)} if topic else {}
                try:
                    if img:
                        await self.bot.send_photo(int(chat_id), photo=img, caption=text[:1024],
                                                  parse_mode="HTML", **where)
                    else:
                        await self.bot.send_message(int(chat_id), text, parse_mode="HTML", **where)
                    done.add(str(chat))
                    sent += 1
                    log.info("announce.posted", chat=chat, url=post.url)
                except Exception as exc:  # noqa: BLE001 - one chat failing must not stop the rest
                    log.warning("announce.failed", chat=chat, url=post.url, error=str(exc)[:160])
            seen[post.url] = sorted(done)
            self._save({"seen": seen})
        return sent

    async def run(self, every_s: int = 600) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001
                log.error("announce.tick_crashed", error=str(exc)[:200])
            await asyncio.sleep(every_s)
