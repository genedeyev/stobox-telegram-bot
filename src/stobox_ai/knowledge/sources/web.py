"""Website source — crawl stobox.io into Documents.

Strategy per seed: try the domain's sitemap.xml first (fast, complete); if none,
fall back to a polite breadth-first crawl restricted to allowed domains, bounded
by max_pages and max_depth, honoring robots.txt. Each page becomes a Document
whose ``source_url`` is the page URL, so answers can cite the live page.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from ...logging import get_logger
from ..models import DocMeta, Document
from .base import USER_AGENT, Fetcher, Source

log = get_logger(__name__)

_SKIP_EXT = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|ico|css|js|zip|gz|mp4|mp3|woff2?|ttf|eot)(\?|$)", re.I
)
_BINARY_DOC = re.compile(r"\.(pdf|docx?|pptx?|xlsx?)(\?|$)", re.I)
_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
# URLs embedded in llms.txt (markdown links or bare) — strip trailing punctuation.
_URL_IN_TEXT = re.compile(r"https?://[^\s)\]<>\"']+")


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _clean_url(url: str) -> str:
    return urldefrag(url)[0].rstrip("/")


class WebSource(Source):
    name = "web"

    def __init__(
        self,
        seeds: list[str],
        allow_domains: list[str] | None = None,
        max_pages: int = 200,
        max_depth: int = 3,
        delay_seconds: float = 0.3,
        category: str = "website",
    ) -> None:
        self.seeds = [_clean_url(s) for s in seeds]
        self.allow = {d.lower() for d in (allow_domains or [_host(s) for s in seeds])}
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.delay = delay_seconds
        self.category = category
        self._robots: dict[str, RobotFileParser] = {}

    async def fetch(self, fetcher: Fetcher) -> list[Document]:
        docs: list[Document] = []
        visited: set[str] = set()

        # Seed the frontier: prefer sitemap, then llms.txt, else the seed itself.
        frontier: list[tuple[str, int]] = []
        for seed in self.seeds:
            discovered = await self._discover_urls(fetcher, seed)
            if discovered:
                frontier += [(u, 0) for u in discovered]
            else:
                frontier.append((seed, 0))

        while frontier and len(docs) < self.max_pages:
            url, depth = frontier.pop(0)
            url = _clean_url(url)
            if url in visited or _host(url) not in self.allow:
                continue
            visited.add(url)
            if _SKIP_EXT.search(url) or _BINARY_DOC.search(url):
                continue
            if not await self._allowed(fetcher, url):
                continue

            status, html, final_url = await self._safe_get(fetcher, url)
            if status != 200 or not html:
                continue
            # Content may live at a redirected URL (e.g. docs.→www.); cite where
            # it actually resolved and resolve links against it.
            final_url = _clean_url(final_url)
            if final_url != url:
                if final_url in visited or _host(final_url) not in self.allow:
                    continue
                visited.add(final_url)

            doc, links = self._parse(final_url, html)
            if doc:
                docs.append(doc)
            if depth < self.max_depth:
                for link in links:
                    if _clean_url(link) not in visited:
                        frontier.append((link, depth + 1))
            if self.delay:
                await asyncio.sleep(self.delay)

        log.info("web.crawled", source=self.name, pages=len(docs), visited=len(visited))
        return docs

    async def _discover_urls(self, fetcher: Fetcher, seed: str) -> list[str]:
        """Find page URLs to crawl: sitemap.xml first, then the AI-oriented
        llms-full.txt / llms.txt inventory (the modern replacement for a sitemap
        — and exactly what stobox.io publishes)."""
        base = f"{urlparse(seed).scheme}://{_host(seed)}"

        # 1) sitemap.xml (+ one level of nested sitemaps)
        status, body, _ = await self._safe_get(fetcher, f"{base}/sitemap.xml")
        if status == 200 and body and "<loc" in body.lower():
            urls = [u for u in _SITEMAP_LOC.findall(body) if _host(u) in self.allow]
            nested = [u for u in urls if u.lower().endswith(".xml")]
            page_urls = [u for u in urls if not u.lower().endswith(".xml")]
            for sm in nested[:20]:
                s, b, _ = await self._safe_get(fetcher, sm)
                if s == 200 and b:
                    page_urls += [u for u in _SITEMAP_LOC.findall(b) if _host(u) in self.allow]
            if page_urls:
                return page_urls[: self.max_pages]

        # 2) llms.txt inventory
        for name in ("llms-full.txt", "llms.txt"):
            s, b, _ = await self._safe_get(fetcher, f"{base}/{name}")
            if s == 200 and b and ("http" in b):
                urls = [
                    _clean_url(u)
                    for u in _URL_IN_TEXT.findall(b)
                    if _host(u) in self.allow
                    and not _SKIP_EXT.search(u)
                    and not _BINARY_DOC.search(u)
                ]
                if urls:
                    log.info("web.discovered_via_llms", file=name, urls=len(set(urls)))
                    return list(dict.fromkeys(urls))[: self.max_pages]
        return []

    async def _allowed(self, fetcher: Fetcher, url: str) -> bool:
        host = _host(url)
        if host not in self._robots:
            rp = RobotFileParser()
            base = f"{urlparse(url).scheme}://{host}"
            status, body, _ = await self._safe_get(fetcher, f"{base}/robots.txt")
            if status == 200 and body:
                rp.parse(body.splitlines())
            else:
                rp.parse([])  # no robots.txt → allow all
            self._robots[host] = rp
        return self._robots[host].can_fetch(USER_AGENT, url)

    async def _safe_get(self, fetcher: Fetcher, url: str) -> tuple[int, str, str]:
        try:
            return await fetcher.get_text(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("web.fetch_failed", url=url, error=str(exc))
            return 0, "", url

    def _parse(self, url: str, html: str) -> tuple[Document | None, list[str]]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        # Collect links before stripping structure.
        links = []
        for a in soup.find_all("a", href=True):
            target = urljoin(url, a["href"])
            if target.startswith("http") and _host(target) in self.allow:
                links.append(target)

        title = (soup.title.string.strip() if soup.title and soup.title.string else url)
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n")).strip()
        if len(text) < 200:  # skip thin/navigation-only pages
            return None, links

        category = "documentation" if "docs." in _host(url) else self.category
        meta = DocMeta(
            title=title[:200],
            source_file=f"web://{url}",
            source_url=url,
            category=category,
            product="Stobox",
            visibility="public",
        )
        return Document(meta=meta, text=text), links
