"""Fixed-text commands, in Telegram HTML. Facts are read from the site at request time.

Voice (Gene, 28.09.2026): friendly, simple words, a few emojis, the new STBU on
Base up front, one contact address only: support@stobox.io. Layout: bold
section titles, every contract labelled and in <code> on its own line with a
blank line around it (one tap copies it), one block of useful links.
"""

from __future__ import annotations

import html

from .format import code, links_block, pool_id
from .rails import IMPERSONATION_WARNING
from .sources.chain import ChainReader, contracts_from_site, is_address, is_private_key
from .sources.site import SiteFacts

SUPPORT = "support@stobox.io"
# Gene, 28.09.2026: Medium is not listed among the official channels.
HIDDEN_CHANNELS = ("medium",)
WARNING = html.escape(IMPERSONATION_WARNING, quote=False)

HELP = (
    "👋 <b>Hi, I'm Stoby</b>, the AI helper of the Stobox community.\n\n"
    "🚀 <b>STBU now lives on Base.</b> Ask me anything about it, or about Stobox, "
    "and I'll answer from stobox.io.\n\n"
    "💬 In the group, just mention me or reply to my message.\n\n"
    "<b>Handy commands</b>\n"
    "🪙 /stbu – the new STBU on Base, contracts and links\n"
    "🔎 /check <code>0xYourAddress</code> – see the STBU in a wallet\n"
    "📰 /blog – the latest articles from the Stobox blog\n"
    "✅ /sources – our official channels\n"
    "📨 /contact – talk to the team"
)
SOURCES_FALLBACK = ("✅ <b>Official links</b>\n\n🌐 https://www.stobox.io\n\n"
                    "🚫 Anyone else using the Stobox name is not us.")
CONTACT = (f"💬 <b>Need a hand?</b>\n\nWrite to {SUPPORT} – the team is happy to help.\n\n"
           "🛡️ We will never DM you first or ask for your seed phrase.")


# The one menu Telegram shows for this bot (setMyCommands at every boot). Only
# commands Stoby really has; ChatKeeper's (/rules /report /ca /base /adminlist
# /partner) are never listed here.
MENU = (
    ("stbu", "The new STBU on Base: contracts and links"),
    ("check", "See the STBU in a wallet: /check 0xAddress"),
    ("blog", "Latest articles from the Stobox blog"),
    ("sources", "Official Stobox channels"),
    ("contact", "Talk to the team"),
    ("help", "What Stoby can do"),
)
KNOWN = {c for c, _ in MENU} | {"start"}
UNKNOWN = ("🤔 I don't have that command. Here's what I can do:\n\n"
           + "\n".join(f"/{c} – {d}" for c, d in MENU))


def blog_text(posts) -> str:
    if not posts:
        return "📰 The latest articles are on https://www.stobox.io/blog"
    rows = [f"• <a href=\"{html.escape(p.url, quote=True)}\">{html.escape(p.title)}</a>" for p in posts[:5]]
    return ("📰 <b>Latest on the Stobox blog</b>\n\n" + "\n\n".join(rows)
            + "\n\n👉 All articles: https://www.stobox.io/blog")


def _sentence(text: str) -> str:
    t = text.strip()
    return t[:1].upper() + t[1:]


def _channel_ok(line: str) -> bool:
    low = line.lower()
    if any(h in low for h in HIDDEN_CHANNELS):
        return False
    return not low.startswith("contact")          # one address only: support@


def sources_text(site: SiteFacts | None) -> str:
    channels = [c for c in (site.bullets("Official channels") if site else []) if _channel_ok(c)]
    if not channels:
        return SOURCES_FALLBACK
    rows = []
    for c in channels:
        name, _, url = c.partition(":")
        rows.append(f"• <b>{html.escape(name.strip())}</b>: {html.escape(url.strip())}")
    return ("✅ <b>Official Stobox channels</b>\n\n" + "\n".join(rows)
            + f"\n• <b>Support</b>: {SUPPORT}\n\n🚫 Anyone else using the Stobox name is not us.")


def stbu_text(site: SiteFacts) -> str:
    live, legacy = contracts_from_site(site)
    pid = pool_id(site)
    claims = site.bullet("STBU, the token", "Legacy claims:") or ""
    out = ["🚀 <b>The new STBU lives on Base!</b>"]
    if live:
        out += ["", "🪙 <b>Token contract</b> · Base · tap to copy", "", code(live)]
    if pid:
        out += ["", "🦄 <b>Official pool</b> · Uniswap v4 · STBU/USDC", "", code(pid)]
    out += ["", links_block(site)]
    if claims:
        out += ["", "📥 <b>Old STBU holders</b>", html.escape(_sentence(claims.split(":", 1)[-1]))]
    discontinued = site.bullet("STBU, the token", "Discontinued")
    if legacy and discontinued:
        out += ["", "🗄️ <b>Old contracts, switched off</b>",
                html.escape(discontinued.split(":", 1)[0].strip()) + ".", ""]
        for chain, addr in legacy.items():
            out += [f"{html.escape(chain)}", code(addr), ""]
        out.pop()
    out += ["", WARNING]
    return "\n".join(out)


async def check_text(arg: str, site: SiteFacts, chain: ChainReader) -> str:
    arg = (arg or "").strip()
    if is_private_key(arg):
        return ("🚨 <b>Careful!</b> That's a private key, not a wallet address.\n\n"
                "Never share it with anyone, me included. If you posted it anywhere, "
                "move your funds to a new wallet right now.")
    if not is_address(arg):
        return "🔎 Send me a public wallet address like this:\n\n/check <code>0xYourAddress</code>"
    live, legacy = contracts_from_site(site)
    if not live:
        return "😕 I can't read the contract right now. You can check it here: https://www.stobox.io/stbu"
    rows = await chain.balances(arg, live, legacy)
    out = ["🔎 <b>Wallet</b>", "", code(arg)]
    for h in rows:
        if h.chain == "Base":
            bal = "couldn't reach Base, try again in a minute" if h.balance is None else f"<b>{h.balance:,.2f}</b>"
            out += ["", f"🚀 <b>New STBU on Base</b>: {bal}"]
    old = [h for h in rows if h.chain != "Base" and h.balance]
    if old:
        out += ["", "🗄️ <b>Old STBU</b> · switched off, can't move to Base any more"]
        out += [f"• {html.escape(h.chain)}: {h.balance:,.2f}" for h in old]
    down = [h.chain for h in rows if h.balance is None and h.chain != "Base"]
    if down:
        out.append(f"<i>(Couldn't reach: {html.escape(', '.join(down))})</i>")
    claims = site.bullet("STBU, the token", "Legacy claims:")
    if claims:
        out += ["", "📥 " + html.escape(_sentence(claims.split(":", 1)[-1]))]
    out += ["", links_block(site, compact=True), "", WARNING]
    return "\n".join(out)
