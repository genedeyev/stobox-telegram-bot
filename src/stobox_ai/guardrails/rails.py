"""Deterministic compliance rails.

These enforce the [CORE] §4 hard rails independently of the model, so a model
slip cannot become a compliance incident:

  * pre-intercepts  — seed-phrase leaks, prompt-injection, price speculation and
    "should I buy" are answered by fixed, safe text (no LLM latitude).
  * post-processing — appends the investment disclaimer and the anti-impersonation
    warning where required, and scrubs/blocks forbidden claims (Class-A,
    "$500M", securities exemptions, "will reach 250M", competitor comparisons).

All matching is conservative and unit-tested; the golden gate (evals/golden.yaml)
locks the behavior in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..logging import get_logger

log = get_logger(__name__)

DISCLAIMER = "This is information, not investment advice."

IMPERSONATION_WARNING = (
    "⚠️ Scam warning: Stobox staff never DM you first and never ask you to "
    "\"validate\" or \"sync\" a wallet. Only trust links from stobox.io. When in "
    "doubt, verify via official channels (/sources)."
)

_SEED_TERMS = re.compile(
    r"\b(seed[\s-]?phrase|secret[\s-]?phrase|recovery[\s-]?phrase|private[\s-]?key|mnemonic)\b",
    re.I,
)
_INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instruction|instructions|rules|prompt|"
    r"guardrail)\b|(system\s+prompt)|(developer\s+mode)|(reveal|print|show|repeat).{0,20}"
    r"(your\s+)?(system\s+)?prompt|jailbreak|DAN\b",
    re.I,
)
_ADMIN_CLAIM = re.compile(r"\b(admin|developer|owner|ceo)\s+(here|says|mode)\b", re.I)
_SPECULATION = re.compile(
    r"\b(moon|pump|10x|100x|1000x|price\s+target|to\s+the\s+moon|when\s+moon)\b"
    r"|\b(will|going\s+to|gonna)\b[^.?!]{0,40}\b(worth|price|value|go\s+up|moon|"
    r"pump|rise|\$\s?\d)\b"
    r"|\bhow\s+high\b|\bexpected\s+(price|value|return|roi)\b",
    re.I,
)
_BUY_SELL = re.compile(
    r"\bshould\s+i\s+(buy|sell|hold|invest|ape|dump)\b|\bis\s+it\s+a\s+good\s+"
    r"(time\s+to\s+)?(buy|investment|sell)\b|\bworth\s+(buying|investing)\b|"
    r"\bhow\s+much\s+should\s+i\s+(buy|invest)\b",
    re.I,
)

_WALLET_TOPIC = re.compile(
    r"\b(migrat|claim|burn|wallet|seed|private\s*key|self[\s-]?custody|"
    r"metamask|ledger|support\s+dm|sync\s+wallet|validate\s+wallet)\b",
    re.I,
)
_INVESTMENT_TOPIC = re.compile(
    r"\b(buy|sell|hold|invest|price|worth|profit|return|yield|dividend|"
    r"valuation|token\s*price|market\s*cap|roi|apy)\b",
    re.I,
)

# Hard-forbidden output substrings → answer is blocked/scrubbed if present,
# UNLESS a "mitigator" shows the phrase is being correctly denied/framed
# (e.g. the model quoting `"will reach" 250M` while explaining the maximum-
# supply framing must NOT be blocked). (from canonicals must_never_claim)
_SUPPLY_MITIGATORS = re.compile(
    r"maximum|at\s+most|ceiling|not\s+necessarily|whatever\s+(amount\s+)?(actually\s+)?migrates"
    r"|can'?t\s+(promise|predict)|cannot\s+(promise|predict)",
    re.I,
)
_FORBIDDEN = [
    (re.compile(r"class[\s-]?a\b", re.I), "Class-A share class", None),
    (re.compile(r"stobox\s+holdings", re.I), "wrong issuer entity", None),
    (re.compile(r"\$\s?500\s?m(illion)?\b|\b500m\+", re.I), "unpublished tokenized volume", None),
    (
        re.compile(r"will\s+reach\s+[\"']?250\s?m|expected\s+supply", re.I),
        "supply speculation",
        _SUPPLY_MITIGATORS,
    ),
]
# Known impostor handles → deterministically scrubbed from output (never shown,
# even in warnings — an official bot must not give fake accounts name recognition).
_SCRUB = [
    (re.compile(r"@?stobox_io\b|@?stobox_official\b", re.I), "an unofficial account"),
]

# Securities-exemption attribution to a Stobox token → block.
_EXEMPTION_ATTR = re.compile(
    r"(offered|issued|sold|available)\s+under\s+(reg(ulation)?\s*[dscfa+]|the\s+eu\s+prospectus)"
    r"|\b(reg\s*d|506\s*\(?c\)?|reg\s*s\b|reg\s*cf|reg\s*a\+?)\b.{0,30}(stbx|stbu|token|offering)",
    re.I,
)


@dataclass(slots=True)
class RailResult:
    text: str
    intercepted: bool = False
    disclaimer_added: bool = False
    impersonation_added: bool = False
    blocked: bool = False
    escalate: bool = False
    category: str | None = None
    violations: list[str] = field(default_factory=list)


class ComplianceRails:
    """Stateless; safe to share across requests."""

    # ---- pre-generation intercepts ------------------------------------- #
    def pre_intercept(self, user_text: str) -> RailResult | None:
        t = user_text or ""

        if _SEED_TERMS.search(t):
            return RailResult(
                text=(
                    "🚨 Never share a seed phrase, recovery phrase, or private key — with "
                    "anyone, including me. If you have already shared it, consider that wallet "
                    "compromised and move your funds to a new wallet immediately.\n\n"
                    + IMPERSONATION_WARNING
                ),
                intercepted=True, escalate=True, category="security",
                impersonation_added=True,
            )

        if _INJECTION.search(t) or _ADMIN_CLAIM.search(t):
            return RailResult(
                text=(
                    "I can't change my instructions, reveal my system prompt, or enable any "
                    "special mode — those rules are fixed. Happy to help with your actual "
                    "question about Stobox, tokenization, or the STBU migration."
                ),
                intercepted=True, category="injection",
            )

        if _BUY_SELL.search(t) or _SPECULATION.search(t):
            return RailResult(
                text=(
                    "I can't give investment advice or make price predictions — Stobox's "
                    "official bot cannot speculate on token value. I can share published facts "
                    "(what STBU/STBX are, the migration, the company valuation page) and point "
                    "you to the team.\n\n" + DISCLAIMER
                ),
                intercepted=True, category="advice",
            )
        return None

    # ---- post-generation processing ------------------------------------ #
    def post_process(self, answer: str, user_text: str) -> RailResult:
        result = RailResult(text=answer or "")

        # 0) Deterministic scrubs — impostor handles etc. never reach the chat.
        for pat, repl in _SCRUB:
            result.text = pat.sub(repl, result.text)

        # 1) Block forbidden claims (compliance-critical): if the model asserted
        #    something it must never say, replace with a safe deflection.
        for pat, label, mitigators in _FORBIDDEN:
            if pat.search(result.text):
                if mitigators and mitigators.search(result.text):
                    continue  # forbidden phrase is being correctly denied/framed
                result.violations.append(label)
        if _EXEMPTION_ATTR.search(result.text):
            result.violations.append("securities-exemption attribution")

        if result.violations:
            log.error("rails.blocked_output", violations=result.violations, q=user_text[:120])
            result.text = (
                "I want to be precise here and I can't confirm that from published sources. "
                "For the exact, current details please see stobox.io or contact the team at "
                "support@stobox.io."
            )
            result.blocked = True
            result.escalate = True
            result.category = "blocked_claim"

        # 2) Anti-impersonation warning on wallet-adjacent topics — unless the
        #    answer already carries one (models often write their own; don't
        #    stack two warnings in one message).
        already_warned = re.search(
            r"never\s+dm|scam|impersonat|staff\s+never", result.text, re.I
        )
        if _WALLET_TOPIC.search(user_text) and not already_warned:
            result.text = result.text.rstrip() + "\n\n" + IMPERSONATION_WARNING
            result.impersonation_added = True

        # 3) Investment disclaimer where relevant.
        if _INVESTMENT_TOPIC.search(user_text + " " + result.text):
            if DISCLAIMER.lower() not in result.text.lower():
                result.text = result.text.rstrip() + "\n\n" + DISCLAIMER
                result.disclaimer_added = True

        return result
