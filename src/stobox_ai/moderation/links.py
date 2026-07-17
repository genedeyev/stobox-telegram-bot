"""Link allowlist policy for community moderation.

Rule (per Gene): a link is OFFICIAL — never removed, even from a regular user —
only when it is a Stobox property. Everything else posted by a NON-admin is
removed (admins are exempted upstream in Moderator.evaluate). Deterministic, no
LLM: a scammer's `stobox-support.io` never passes as "from Stobox".

Official =
  * any host on the stobox.io domain (stobox owns it: www./app./docs. …), OR
  * an exact official handle on a shared platform (x.com/StoboxCompany,
    t.me/stobox_community, youtube.com/@stobox, …) — matched by host + first
    path segment, so x.com/ScamStobox is NOT official.
Extra trusted entries come from config (moderation.link_policy.allow).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Official handles as (host, first-path-segment-lowercased). Derived from the
# canonical official_links; kept here so moderation never depends on the
# canonicals loader. Config can extend this list.
_DEFAULT_HANDLES: tuple[tuple[str, str], ...] = (
    ("x.com", "stoboxcompany"),
    ("twitter.com", "stoboxcompany"),
    ("t.me", "stobox_community"),
    ("t.me", "stobox"),
    ("youtube.com", "@stobox"),
    ("linkedin.com", "company"),          # linkedin.com/company/stobox → seg 'company'; refined below
    ("github.com", "stoboxtechnologies"),
    ("facebook.com", "stoboxforbusiness"),
)
# Handles needing a two-segment match (host/seg1/seg2), lowercased.
_DEFAULT_HANDLES2: tuple[tuple[str, str, str], ...] = (
    ("linkedin.com", "company", "stobox"),
)
# Whole hosts that are official (Stobox-owned outside the stobox.io domain).
_DEFAULT_HOSTS: tuple[str, ...] = ("stobox-platform.medium.com",)

# Link-like tokens Telegram will auto-link: explicit http(s), t.me/, www., and
# BARE domains with a common TLD (scammers post "wallet-sync.io" with no scheme).
# The TLD list is curated to avoid false positives on filenames/versions
# ("app.py", "v2.0", "index.html" — none of those TLDs are listed).
_LINK_TLDS = (
    "io|com|net|org|xyz|finance|app|co|me|link|click|vip|top|site|online|pro|"
    "info|biz|dev|fi|cc|gg|ai|to|ly|sh|id|so|fun|live|world|network|exchange|wtf"
)
_URL_RE = re.compile(
    r"(https?://[^\s<>\"')]+"
    r"|(?<!@)\bt\.me/[^\s<>\"')]+"
    r"|\bwww\.[^\s<>\"')]+"
    rf"|(?<![@\w.])(?:[a-z0-9][a-z0-9-]*\.)+(?:{_LINK_TLDS})\b(?:/[^\s<>\"')]*)?)",
    re.I,
)


def _norm_host(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


class LinkPolicy:
    def __init__(self, allow: list[str] | None = None) -> None:
        # Config `allow` entries: a bare host ("example.com") or host/path-prefix
        # ("x.com/StoboxCompany"). Merged with the built-in Stobox handles.
        self._hosts = set(_DEFAULT_HOSTS)
        self._handles = set(_DEFAULT_HANDLES)
        self._handles2 = set(_DEFAULT_HANDLES2)
        for entry in allow or []:
            entry = entry.strip().lower().lstrip("/")
            if not entry:
                continue
            if "/" in entry:
                host, _, rest = entry.partition("/")
                segs = [s for s in rest.split("/") if s]
                host = _norm_host(host)
                if len(segs) >= 2:
                    self._handles2.add((host, segs[0], segs[1]))
                elif segs:
                    self._handles.add((host, segs[0]))
            else:
                self._hosts.add(_norm_host(entry))

    def is_official(self, url: str) -> bool:
        raw = url if "://" in url else f"https://{url}"
        p = urlparse(raw)
        host = _norm_host(p.netloc or p.path.split("/")[0])
        if not host:
            return False
        # Stobox-owned domain — any subdomain, any path.
        if host == "stobox.io" or host.endswith(".stobox.io"):
            return True
        if host in self._hosts:
            return True
        segs = [s for s in p.path.split("/") if s]
        seg1 = segs[0].lower() if segs else ""
        if (host, seg1) in self._handles:
            return True
        seg2 = segs[1].lower() if len(segs) > 1 else ""
        return (host, seg1, seg2) in self._handles2

    def disallowed(self, text: str) -> list[str]:
        """URLs in `text` that are NOT official Stobox links (deduped)."""
        out: list[str] = []
        for m in _URL_RE.findall(text or ""):
            url = m[0] if isinstance(m, tuple) else m
            url = url.rstrip(".,!?;:)")
            if not self.is_official(url) and url not in out:
                out.append(url)
        return out
