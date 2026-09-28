"""Telegram layout: clean HTML, tap-to-copy contracts, one block of useful links.

Gene, 28.09.2026: answers structured and easy to read; a contract always shown
as a contract, on its own line with space around it, copyable in one tap; the
site's STBU page, the readiness score, the issuer's Uniswap pool and CoinGecko
always at hand.

The model writes plain text with light markdown; `to_html` turns it into the
small HTML subset Telegram accepts. Everything else is escaped, so a stray `<`
from the model or a quoted user can never break the message.
"""

from __future__ import annotations

import html
import re

from .sources.site import SiteFacts

# Not facts about Stobox: where to look. CoinGecko lists STBU under this id with
# the Base contract (API check 28.09.2026). CoinMarketCap still shows a legacy
# Ethereum contract next to the Base one, so it is left out until its review.
COINGECKO = "https://www.coingecko.com/en/coins/stobox-token"
STBU_PAGE = "https://www.stobox.io/stbu"
SAFETY_PAGE = "https://www.stobox.io/stbu/safety"
READINESS = "https://www.stobox.io/readiness"
UNISWAP_POOL = "https://app.uniswap.org/explore/pools/base/{pool}"

_ADDR = re.compile(r"(?<![0-9a-fA-Fx/])0x[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?(?![0-9a-fA-F])")
_POOL = re.compile(r"pool id\s+`(0x[0-9a-fA-F]{64})`")
_STBU_TOPIC = re.compile(r"(?i)\b(stbu|token|price|buy|sell|trade|trading|pool|uniswap|coingecko|"
                         r"contract|claim|migrat|legacy|chart|market)\b")


def pool_id(site: SiteFacts) -> str | None:
    m = _POOL.search(site.sections.get("STBU, the token", ""))
    return m.group(1) if m else None


def link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


def code(value: str) -> str:
    return f"<code>{html.escape(value)}</code>"


def links_block(site: SiteFacts | None, *, compact: bool = False, safety: bool = True) -> str:
    rows = [f"🌐 {link(STBU_PAGE, 'STBU on stobox.io')}"]
    pid = pool_id(site) if site else None
    if pid:
        rows.append(f"🦄 {link(UNISWAP_POOL.format(pool=pid), 'Official pool on Uniswap')}")
    rows.append(f"🦎 {link(COINGECKO, 'STBU on CoinGecko')}")
    if safety:
        rows.append(f"🛡️ {link(SAFETY_PAGE, 'Check the real pool before you trade')}")
    if not compact:
        rows.append(f"📋 {link(READINESS, 'Readiness score for your company')}")
    return "🔗 <b>Useful links</b>\n" + "\n".join(rows)


def is_stbu_topic(question: str) -> bool:
    return bool(_STBU_TOPIC.search(question or ""))


def to_html(text: str) -> str:
    """Model text → Telegram HTML. Addresses become tap-to-copy <code> lines
    with a blank line around them; **bold** and `code` are kept; `- ` bullets
    become •; everything else is escaped."""
    text = (text or "").strip()
    # Put every bare address on its own line, framed by blank lines.
    text = re.sub(r"`?(" + _ADDR.pattern + r")`?", r"\n\n\1\n\n", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)
    out = _ADDR.sub(lambda m: f"<code>{m.group(0)}</code>", out)
    return re.sub(r"(?m)^[-*] +", "• ", out)


_SITE_URL = re.compile(r"https://(?:www\.)?stobox\.io(?:/[^\s\"<>)]*)?")


def preview_url(text: str) -> str | None:
    """The page Telegram should show as a small preview card: the first stobox.io
    link in the message (its og:image is the site's own artwork). External links
    (Uniswap, CoinGecko) never get the card."""
    m = _SITE_URL.search(text or "")
    return m.group(0).rstrip(".,;:!?") if m else None
