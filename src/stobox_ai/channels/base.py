"""Channel adapter contract.

A channel translates its native events into :class:`IncomingMessage`, calls the
shared engine, and renders :class:`AgentResponse` back — including executing
moderation actions. Keeping this interface tiny is what makes the platform
genuinely channel-agnostic.
"""

from __future__ import annotations

import abc

from ..core.engine import AgentEngine
from ..core.types import AgentResponse, Citation


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
            label = c.title + (f" — {c.source_url}" if c.source_url else "")
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
