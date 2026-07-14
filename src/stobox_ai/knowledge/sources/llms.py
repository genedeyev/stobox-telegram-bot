"""llms.txt source — ingest a site's AI-oriented knowledge files.

Modern sites publish ``/llms.txt`` (a curated overview + link inventory) and
``/llms-full.txt`` (the full reference content), authored specifically for LLMs.
stobox.io publishes both. These are the single highest-quality, canonical source
for the bot — curated by the Stobox team, kept current, and free of marketing
chrome — so they're ingested at high confidence.

The ``.txt`` files are Markdown; the semantic chunker splits them by their
``##`` sections automatically, so citations land on the right topic.
"""

from __future__ import annotations

from ...logging import get_logger
from ..models import DocMeta, Document
from .base import Fetcher, Source

log = get_logger(__name__)

_FILES = ("llms-full.txt", "llms.txt")


class LlmsTxtSource(Source):
    name = "llms"

    def __init__(self, hosts: list[str], confidence: float = 1.0) -> None:
        # Accept bare hosts or full URLs; normalize to https://host
        self.bases = [self._base(h) for h in hosts]
        self.confidence = confidence

    @staticmethod
    def _base(host: str) -> str:
        h = host.strip().rstrip("/")
        if h.startswith("http"):
            return h
        return f"https://{h}"

    async def fetch(self, fetcher: Fetcher) -> list[Document]:
        docs: list[Document] = []
        for base in self.bases:
            for name in _FILES:
                url = f"{base}/{name}"
                try:
                    status, body, final_url = await fetcher.get_text(url)
                except Exception as exc:  # noqa: BLE001
                    log.warning("llms.fetch_failed", url=url, error=str(exc))
                    continue
                # Guard against soft-404s that return an HTML shell.
                if status != 200 or not body or len(body) < 200:
                    continue
                if "<html" in body[:500].lower() or "<!doctype" in body[:200].lower():
                    log.info("llms.skipped_html", url=url)
                    continue
                docs.append(self._to_doc(final_url or url, name, body))
                log.info("llms.ingested", url=url, chars=len(body))
                if name == "llms-full.txt":
                    break  # full reference supersedes the short one for this host
        return docs

    def _to_doc(self, url: str, name: str, body: str) -> Document:
        # PUBLIC citation = the website, never the .txt machine file. The txt
        # URL is kept internally (extra.fetched_from) for sync bookkeeping.
        from urllib.parse import urlparse

        p = urlparse(url)
        site_url = f"{p.scheme}://{p.netloc}"
        meta = DocMeta(
            title="Stobox — Official Website Reference",
            source_file=f"llms://{url}",
            source_url=site_url,
            category="documentation",
            product="Stobox",
            visibility="public",
            confidence=self.confidence,
            extra={"kind": "llms.txt", "file": name, "fetched_from": url},
        )
        return Document(meta=meta, text=body)
