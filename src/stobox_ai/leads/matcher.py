"""Match an issuer's profile (asset + jurisdiction) to the right PUBLISHED
Stobox resources.

This is deliberately grounded and non-committal. It never invents case studies,
client names, outcomes, or jurisdiction-specific legal conclusions — those would
be fabrication and unlicensed advice. Instead it gives general, educational
framing and routes to real official resources (the free Readiness Score, the
learn pages, Compass) plus the standing "confirm with your counsel" line.

If real case studies are indexed into the knowledge base later, the adapter can
append a retrieved link — but the deterministic core here stays fabrication-free.
"""

from __future__ import annotations

# Real, official URLs only (mirrors canonicals.yaml official links).
READINESS_URL = "https://www.stobox.io/compass"
LEARN_STV3_URL = "https://www.stobox.io/learn/erc-3643-vs-stv3-transfer-restriction-models"
INTELLIGENCE_URL = "https://www.stobox.io/intelligence"

# General, promise-free education per asset type. Keyed by AXIS asset values.
_ASSET = {
    "real_estate": ("real estate",
        "the most common RWA category — issuers usually want fractional ownership, "
        "faster settlement, and a compliant on-chain cap table."),
    "fund": ("funds / PE vehicles",
        "tokenizing LP interests can simplify administration, transfers, and investor "
        "reporting."),
    "equity": ("company equity",
        "equity can be issued as compliant security tokens with an on-chain registry — "
        "handy for cap-table management and secondary transfers."),
    "credit": ("private credit / debt",
        "debt instruments tokenize to streamline issuance, servicing, and reporting."),
}
_ASSET_DEFAULT = ("your asset",
    "most real-world assets tokenize to gain a compliant on-chain registry and easier "
    "transfer/reporting.")

# Jurisdiction framing stays high-level and always defers to the issuer's counsel —
# never a specific legal conclusion.
_JURISDICTION = {
    "us": ("the US",
        "US offerings are typically structured under securities exemptions and need your "
        "own securities counsel; Stobox's permissioned standards are built for regulated "
        "issuance."),
    "eu": ("the EU",
        "EU offerings follow the applicable prospectus/exemption regime; the specifics "
        "depend on your structure and counsel."),
    "uk": ("the UK",
        "UK offerings follow the applicable FCA regime; specifics depend on your structure "
        "and counsel."),
    "other": ("your region",
        "the right regime depends on where your asset and investors sit — your counsel "
        "confirms it."),
}
_JURISDICTION_DEFAULT = _JURISDICTION["other"]


def match(asset: str = "", jurisdiction: str = "", first_name: str = "") -> str:
    """A tailored, compliance-safe resource pack for an issuer profile (HTML)."""
    a_label, a_note = _ASSET.get(asset, _ASSET_DEFAULT)
    j_label, j_note = _JURISDICTION.get(jurisdiction, _JURISDICTION_DEFAULT)
    hi = f"{first_name}, here's" if first_name else "Here's"
    return (
        f"📚 <b>{hi} a tailored path for {a_label} in {j_label}</b>\n\n"
        f"• <b>Asset:</b> {a_note}\n"
        f"• <b>Jurisdiction:</b> {j_note}\n\n"
        "<b>Best next steps</b>\n"
        f"1. Free <b>Readiness Score</b> — 25 questions, no card, the methodology Compass "
        f"uses: {READINESS_URL}\n"
        f"2. How Stobox keeps tokens compliant (STV3 / ERC-3643 transfer rules): "
        f"{LEARN_STV3_URL}\n"
        f"3. Organize your data room first with Intelligence: {INTELLIGENCE_URL}\n\n"
        "Want the team to look at your specific case? Share your email with "
        "<code>/email you@address.com</code>.\n\n"
        "<i>General information, not legal or investment advice — your counsel and the "
        "Readiness Score confirm what actually fits.</i>"
    )


def resources_overview() -> str:
    """A general resource menu for /resources when we don't yet know their profile."""
    return (
        "📚 <b>Stobox resources</b>\n\n"
        f"• <b>Free Readiness Score</b> (25 Q, no card): {READINESS_URL}\n"
        f"• <b>How compliant tokens work</b> (STV3 / ERC-3643): {LEARN_STV3_URL}\n"
        f"• <b>Organize your data room</b> (Intelligence): {INTELLIGENCE_URL}\n\n"
        "Want a path tailored to <b>your</b> asset &amp; jurisdiction? Run a quick "
        "30-second fit check with /qualify and I'll match resources to your case.\n\n"
        "<i>General information, not legal or investment advice.</i>"
    )
