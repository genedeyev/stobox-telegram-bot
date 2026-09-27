"""Fixed-text commands. Facts in them are read from the site at request time."""

from __future__ import annotations

from .rails import IMPERSONATION_WARNING
from .sources.chain import ChainReader, contracts_from_site, is_address, is_private_key
from .sources.site import SiteFacts

HELP = (
    "I'm Stoby, the AI assistant of the Stobox community. Ask me about Stobox, its products "
    "and the STBU token; I answer from the published record at stobox.io.\n\n"
    "In the group, mention me or reply to my message. Commands:\n"
    "/stbu – the STBU record\n/check 0xAddress – read a wallet's STBU\n"
    "/sources – official links\n/contact – reach the team"
)
# Until the site publishes its channels (stobox-v15#938), the only fact the bot
# keeps of its own is the website address itself.
SOURCES_FALLBACK = ("Official Stobox links are listed on https://www.stobox.io. "
                    "Anything else claiming to be Stobox is not us.")


def sources_text(site: SiteFacts | None) -> str:
    channels = site.bullets("Official channels") if site else []
    if not channels:
        return SOURCES_FALLBACK
    return ("Official Stobox channels, from the published record:\n" + "\n".join(channels)
            + "\n\nAnything else claiming to be Stobox is not us.")
CONTACT = ("The team: info@stobox.io or https://www.stobox.io/contact. "
           "For a specific STBU burn or claim: support@stobox.io.")


def stbu_text(site: SiteFacts) -> str:
    live, legacy = contracts_from_site(site)
    claims = site.bullet("STBU, the token", "Legacy claims:") or ""
    lines = ["STBU, from the published record:"]
    if live:
        lines.append(f"Live contract on Base: {live}")
    if claims:
        lines.append(claims)
    discontinued = site.bullet("STBU, the token", "Discontinued")
    if legacy and discontinued:
        lines.append(discontinued.split(":", 1)[0] + ": "
                     + "; ".join(f"{k} {v}" for k, v in legacy.items()))
    lines += ["Record with figures from the chain: https://www.stobox.io/stbu",
              "Before any trade: https://www.stobox.io/stbu/safety", "", IMPERSONATION_WARNING]
    return "\n".join(lines)


async def check_text(arg: str, site: SiteFacts, chain: ChainReader) -> str:
    arg = (arg or "").strip()
    if is_private_key(arg):
        return ("That looks like a private key, not a wallet address. Never share it with "
                "anyone, including me. If you posted it anywhere, treat that wallet as "
                "compromised and move your funds to a new wallet now.")
    if not is_address(arg):
        return "Send a public wallet address: /check 0xYourAddress (42 characters, starts with 0x)."
    live, legacy = contracts_from_site(site)
    if not live:
        return "I can't read the published contract right now. The record is https://www.stobox.io/stbu"
    rows = await chain.balances(arg, live, legacy)
    short = f"{arg[:6]}…{arg[-4:]}"
    out = [f"STBU check for {short}"]
    for h in rows:
        if h.chain == "Base":
            out.append(f"Base (live STBU): {'unreachable' if h.balance is None else f'{h.balance:,.2f}'}")
    old = [h for h in rows if h.chain != "Base" and h.balance]
    if old:
        discontinued = site.bullet("STBU, the token", "Discontinued") or "Discontinued legacy tokens"
        out.append("Legacy tokens named STBU (" + discontinued.split(":", 1)[0].lower()
                   + "; not STBU, cannot be migrated):")
        out += [f"{h.chain}: {h.balance:,.2f}" for h in old]
    down = [h.chain for h in rows if h.balance is None and h.chain != "Base"]
    if down:
        out.append(f"(Couldn't reach: {', '.join(down)})")
    claims = site.bullet("STBU, the token", "Legacy claims:")
    if claims:
        out += ["", claims]
    out += ["", IMPERSONATION_WARNING]
    return "\n".join(out)
