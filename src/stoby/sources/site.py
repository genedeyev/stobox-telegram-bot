"""The live site as the source of every Stobox fact: stobox.io/llms-full.txt.

The site outranks SIG on STBU (SIG was stale on the token until 27.09.2026). The
file is fetched with a short cache, a last good copy is kept on the volume, and
it is used for at most `site_stale_hours` when the site is down. If the file is
unreadable or the STBU section is missing, Stoby knows nothing and says so.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

REQUIRED = ("What is true, and what is not", "STBU, the token")
# Every section but the per-page index ("Every page", ~140 KB): the rest is
# ~17 KB and goes into the cached system prompt whole.
SKIPPED = ("Every page",)
ADDR = re.compile(r"(?<![0-9a-fA-Fx])0x[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?(?![0-9a-fA-F])")


class SiteUnavailable(Exception):
    """No usable copy of the site: Stoby must not answer from the model."""


@dataclass(frozen=True)
class SiteFacts:
    header: str
    sections: dict[str, str]
    fetched_at: float
    source: str = "https://www.stobox.io/llms-full.txt"
    addresses: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def parse(cls, text: str, fetched_at: float, source: str = "") -> SiteFacts:
        parts = re.split(r"(?m)^## ", text)
        header = parts[0].strip()
        sections: dict[str, str] = {}
        for p in parts[1:]:
            title, _, body = p.partition("\n")
            sections[title.strip()] = body.strip()
        missing = [s for s in REQUIRED if s not in sections]
        if missing:
            raise SiteUnavailable(f"site file lacks sections: {missing}")
        used = "\n".join(v for k, v in sections.items() if k not in SKIPPED)
        addrs = frozenset(a.lower() for a in ADDR.findall(used))
        return cls(header=header, sections=sections, fetched_at=fetched_at,
                   source=source or cls.source, addresses=addrs)

    def block(self) -> str:
        """The site text Stoby answers from, as one tagged block."""
        out = [self.header]
        for s, body in self.sections.items():
            if s not in SKIPPED:
                out.append(f"## {s}\n{body}")
        return "\n\n".join(out)

    def bullets(self, section: str) -> list[str]:
        """Bullets of a section with their wrapped continuation lines joined."""
        out: list[str] = []
        for line in self.sections.get(section, "").splitlines():
            if line.startswith("- "):
                out.append(line[2:].strip())
            elif line.strip() and out:
                out[-1] += " " + line.strip()
        return out

    def bullet(self, section: str, starts: str) -> str | None:
        return next((b for b in self.bullets(section) if b.startswith(starts)), None)

    @property
    def where_to_buy(self) -> str | None:
        line = self.bullet("STBU, the token", "Where to buy:")
        return line.split(":", 1)[1].strip() if line else None

    def age_hours(self, now: float | None = None) -> float:
        return ((now or time.time()) - self.fetched_at) / 3600


class SiteSource:
    def __init__(self, url: str, state_dir: Path, cache_minutes: int = 10,
                 stale_hours: int = 24, client: httpx.AsyncClient | None = None) -> None:
        self.url = url
        self.cache_s = max(cache_minutes * 60, 1)
        self.stale_s = stale_hours * 3600
        self.copy = state_dir / "site_llms_full.txt"
        self.meta = state_dir / "site_llms_full.json"
        self.client = client
        self._facts: SiteFacts | None = None
        self._etag: str | None = None

    async def facts(self, now: float | None = None) -> SiteFacts:
        now = now or time.time()
        if self._facts and now - self._facts.fetched_at < self.cache_s:
            return self._facts
        try:
            fresh = await self._fetch(now)
            if fresh:
                self._facts = fresh
                return fresh
        except (httpx.HTTPError, SiteUnavailable):
            pass
        last = self._facts or self._load_copy()
        if last and now - last.fetched_at < self.stale_s:
            self._facts = last
            return last
        raise SiteUnavailable("site unreachable and no copy younger than the stale limit")

    async def _fetch(self, now: float) -> SiteFacts | None:
        headers = {"If-None-Match": self._etag} if self._etag and self._facts else {}
        client = self.client or httpx.AsyncClient(timeout=10, follow_redirects=True)
        try:
            r = await client.get(self.url, headers=headers,
                                 params={"t": int(now // self.cache_s)})
        finally:
            if self.client is None:
                await client.aclose()
        if r.status_code == 304 and self._facts:
            return SiteFacts(**{**self._facts.__dict__, "fetched_at": now})
        r.raise_for_status()
        facts = SiteFacts.parse(r.text, now, self.url)
        self._etag = r.headers.get("etag")
        self._save_copy(r.text, now)
        return facts

    def _save_copy(self, text: str, now: float) -> None:
        try:
            self.copy.parent.mkdir(parents=True, exist_ok=True)
            self.copy.write_text(text, encoding="utf-8")
            self.meta.write_text(json.dumps({"fetched_at": now, "url": self.url}))
        except OSError:
            pass

    def _load_copy(self) -> SiteFacts | None:
        try:
            meta = json.loads(self.meta.read_text())
            return SiteFacts.parse(self.copy.read_text(encoding="utf-8"),
                                   float(meta["fetched_at"]), meta.get("url", self.url))
        except (OSError, ValueError, KeyError, SiteUnavailable):
            return None
