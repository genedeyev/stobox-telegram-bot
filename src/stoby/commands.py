"""Fixed-text commands. Facts in them are read from the site at request time.

Voice (Gene, 28.09.2026): friendly, simple words, a few emojis, the new STBU on
Base up front, one contact address only: support@stobox.io.
"""

from __future__ import annotations

from .rails import IMPERSONATION_WARNING
from .sources.chain import ChainReader, contracts_from_site, is_address, is_private_key
from .sources.site import SiteFacts

SUPPORT = "support@stobox.io"
# Gene, 28.09.2026: Medium is not listed among the official channels.
HIDDEN_CHANNELS = ("medium",)

HELP = (
    "Hi, I'm Stoby 👋 the AI helper of the Stobox community.\n\n"
    "🚀 STBU now lives on Base. Ask me anything about it, or about Stobox, "
    "and I'll answer from stobox.io.\n\n"
    "In the group, just mention me or reply to my message.\n\n"
    "Handy commands:\n"
    "🪙 /stbu – the new STBU on Base\n"
    "🔎 /check 0xYourAddress – see the STBU in a wallet\n"
    "✅ /sources – our official links\n"
    f"💬 /contact – talk to the team ({SUPPORT})"
)
SOURCES_FALLBACK = ("✅ Our official links are on https://www.stobox.io\n\n"
                    "🚫 Anyone else using the Stobox name is not us.")
CONTACT = (f"💬 Need a hand? Write to {SUPPORT}, the team is happy to help.\n\n"
           "🛡️ We will never DM you first or ask for your seed phrase.")


def _channel_ok(line: str) -> bool:
    low = line.lower()
    if any(h in low for h in HIDDEN_CHANNELS):
        return False
    return not low.startswith("contact")          # one address only: support@


def sources_text(site: SiteFacts | None) -> str:
    channels = [c for c in (site.bullets("Official channels") if site else []) if _channel_ok(c)]
    if not channels:
        return SOURCES_FALLBACK
    return ("✅ Official Stobox channels:\n\n" + "\n".join(f"• {c}" for c in channels)
            + f"\n• Support: {SUPPORT}\n\n🚫 Anyone else using the Stobox name is not us.")


def stbu_text(site: SiteFacts) -> str:
    live, legacy = contracts_from_site(site)
    claims = site.bullet("STBU, the token", "Legacy claims:") or ""
    lines = ["🚀 The new STBU lives on Base!"]
    if live:
        lines += ["", f"🪙 Contract on Base:\n{live}"]
    if claims:
        lines += ["", f"📥 {claims}"]
    discontinued = site.bullet("STBU, the token", "Discontinued")
    if legacy and discontinued:
        lines += ["", "🗄️ The old STBU contracts are switched off. "
                  + discontinued.split(":", 1)[0] + ":\n"
                  + "\n".join(f"• {k}: {v}" for k, v in legacy.items())]
    lines += ["", "📊 Live figures: https://www.stobox.io/stbu",
              "🛡️ Before you trade, check the one real pool: https://www.stobox.io/stbu/safety",
              "", IMPERSONATION_WARNING]
    return "\n".join(lines)


async def check_text(arg: str, site: SiteFacts, chain: ChainReader) -> str:
    arg = (arg or "").strip()
    if is_private_key(arg):
        return ("🚨 Careful! That's a private key, not a wallet address. Never share it with "
                "anyone, me included. If you posted it anywhere, move your funds to a new "
                "wallet right now.")
    if not is_address(arg):
        return "🔎 Send me a public wallet address like this: /check 0xYourAddress"
    live, legacy = contracts_from_site(site)
    if not live:
        return "😕 I can't read the contract right now. You can check it here: https://www.stobox.io/stbu"
    rows = await chain.balances(arg, live, legacy)
    short = f"{arg[:6]}…{arg[-4:]}"
    out = [f"🔎 Wallet {short}"]
    for h in rows:
        if h.chain == "Base":
            bal = "couldn't reach Base, try again in a minute" if h.balance is None else f"{h.balance:,.2f}"
            out.append(f"🚀 New STBU on Base: {bal}")
    old = [h for h in rows if h.chain != "Base" and h.balance]
    if old:
        out += ["", "🗄️ Old STBU (switched off, can't be moved to Base any more):"]
        out += [f"• {h.chain}: {h.balance:,.2f}" for h in old]
    down = [h.chain for h in rows if h.balance is None and h.chain != "Base"]
    if down:
        out.append(f"(Couldn't reach: {', '.join(down)})")
    claims = site.bullet("STBU, the token", "Legacy claims:")
    if claims:
        out += ["", f"📥 {claims}"]
    out += ["", IMPERSONATION_WARNING]
    return "\n".join(out)
