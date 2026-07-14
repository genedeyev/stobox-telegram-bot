"""Source + Fetcher abstractions.

``Fetcher`` is a tiny async HTTP interface so sources are testable without
network (inject a fake). ``HttpxFetcher`` is the production implementation with
polite defaults (user-agent, timeout, retry, redirects).
"""

from __future__ import annotations

import abc
from typing import Any, Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from ...logging import get_logger
from ..models import Document

log = get_logger(__name__)

USER_AGENT = "StoboxAI-KnowledgeBot/0.1 (+https://stobox.io; community assistant)"


class Fetcher(Protocol):
    """Minimal async HTTP surface used by sources.

    ``get_text`` returns ``(status, text, final_url)`` — the final URL after any
    redirects, so callers cite and resolve links against where the content
    actually lives (e.g. docs.stobox.io → www.stobox.io).
    """

    async def get_text(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        ...

    async def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        ...

    async def aclose(self) -> None:
        ...


class HttpxFetcher:
    """Production Fetcher backed by httpx (lazy import; polite defaults)."""

    def __init__(self, timeout: float = 20.0) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def get_text(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        resp = await self._client.get(url, headers=headers)
        return resp.status_code, resp.text, str(resp.url)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        resp = await self._client.get(url, headers=headers)
        try:
            return resp.status_code, resp.json()
        except Exception:  # noqa: BLE001 - non-JSON body
            return resp.status_code, None

    async def aclose(self) -> None:
        await self._client.aclose()


class Source(abc.ABC):
    """A remote knowledge source that yields Documents."""

    name: str = "source"

    @abc.abstractmethod
    async def fetch(self, fetcher: Fetcher) -> list[Document]:
        ...
