"""Remote knowledge sources.

Pluggable ingesters that produce :class:`~stobox_ai.knowledge.models.Document`
objects from external systems, then flow through the same
chunk → embed → index pipeline as local ``docs/`` files:

  * :class:`WebSource`    — crawl stobox.io (llms.txt inventory or BFS).
  * :class:`GitHubSource` — auto-discover and pull the StoboxTechnologies repos
    (Markdown, docs, and Solidity/TS/… source).

The HTTP layer (:class:`Fetcher`) is injectable so crawling logic is unit-tested
offline with fixtures — no network in tests.
"""

from .base import Fetcher, HttpxFetcher, Source
from .github import GitHubSource
from .llms import LlmsTxtSource
from .web import WebSource

__all__ = ["Source", "Fetcher", "HttpxFetcher", "WebSource", "GitHubSource", "LlmsTxtSource"]
