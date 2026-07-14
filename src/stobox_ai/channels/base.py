"""Channel adapter contract.

A channel translates its native events into :class:`IncomingMessage`, calls the
shared engine, and renders :class:`AgentResponse` back — including executing
moderation actions. Keeping this interface tiny is what makes the platform
genuinely channel-agnostic.
"""

from __future__ import annotations

import abc
import re
from urllib.parse import urlparse

from ..core.engine import AgentEngine
from ..core.types import AgentResponse, Citation

# Machine/artifact files are never valid public references — cite the site root
# instead (llms.txt, sitemaps, raw data files).
_NON_CITABLE = re.compile(r"\.(txt|xml|json|yaml|yml|csv|md)(\?|#|$)", re.I)


def public_citation_url(url: str | None) -> str | None:
    """Return a user-presentable URL: real pages pass through; machine files
    collapse to their site root; raw GitHub blobs are already page URLs."""
    if not url:
        return None
    if _NON_CITABLE.search(url) and "github.com" not in url:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else None
    return url


class Channel(abc.ABC):
    name: str = "base"

    def __init__(self, engine: AgentEngine) -> None:
        self.engine = engine

    @abc.abstractmethod
    async def start(self) -> None:
        """Begin consuming events (long-poll / webhook / gateway)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        ...

    @staticmethod
    def render_citations(response: AgentResponse) -> str:
        """Compact citation footer: one line per source document (deduped by
        title, max 3) — chat readers don't need per-section variants."""
        if not response.citations:
            return ""
        lines = []
        for c in _dedupe(response.citations):
            url = public_citation_url(c.source_url)
            label = c.title + (f" — {url}" if url else "")
            lines.append(f"• {label}")
        return "\n\n📚 Sources:\n" + "\n".join(lines)


def _dedupe(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for c in citations:
        if c.title not in seen:
            seen.add(c.title)
            out.append(c)
        if len(out) >= 3:
            break
    return out
