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

# The single official destination we point issuers to (keep it to one link).
READINESS_URL = "https://www.stobox.io/compass"

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


def _cap(s: str) -> str:
    return (s[:1].upper() + s[1:]) if s else s


def match(asset: str = "", jurisdiction: str = "", first_name: str = "") -> str:
    """A tailored, compliance-safe resource nudge for an issuer profile (HTML)."""
    a_label, a_note = _ASSET.get(asset, _ASSET_DEFAULT)
    j_label, j_note = _JURISDICTION.get(jurisdiction, _JURISDICTION_DEFAULT)
    name = f"{first_name}, " if first_name else ""
    return (
        f"{name}here's the short version for {a_label} in {j_label}. "
        f"{_cap(a_note)} {_cap(j_note)}\n\n"
        f"Honestly, the best first move is the free Readiness Score — it runs the same "
        f"methodology Compass uses and shows you exactly where you stand: {READINESS_URL}. "
        "Want the team to look at your specific case? Just share your email with "
        "<code>/email</code>.\n\n"
        "<i>General info, not legal or investment advice — your counsel and the Readiness "
        "Score confirm what actually fits.</i>"
    )


def resources_overview() -> str:
    """A short, human resources pointer for /resources when we don't know the profile."""
    return (
        "Happy to point you the right way. If you're exploring tokenization, the best "
        f"starting point is the free Readiness Score — 25 quick questions, no card: "
        f"{READINESS_URL}. Want it tailored to your asset and jurisdiction? Run a quick "
        "fit check with /qualify and I'll match it to your case.\n\n"
        "<i>General info, not legal or investment advice.</i>"
    )
