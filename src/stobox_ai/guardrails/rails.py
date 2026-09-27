"""Deterministic compliance rails.

These enforce the [CORE] §4 hard rails independently of the model, so a model
slip cannot become a compliance incident:

  * pre-intercepts – seed-phrase leaks, prompt-injection, price speculation and
    "should I buy" are answered by fixed, safe text (no LLM latitude).
  * post-processing – appends the investment disclaimer and the anti-impersonation
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


# --- Output link / address rail (stage 0 security review, 27.09.2026) -------
# The prompt and the regexes are public, and recall feeds other people's old
# messages into the answer, so a scammer's link or address could come back out
# in Stoby's voice. Deterministic rule: only official hosts and only addresses
# that appear in canonicals.yaml ever reach the chat.
_ALLOWED_HOSTS = ("stobox.io",)                      # + every subdomain
_ALLOWED_PREFIXES = (
    "x.com/stoboxcompany", "twitter.com/stoboxcompany", "t.me/stobox_community",
    "linkedin.com/company/stobox", "youtube.com/@stobox", "github.com/stoboxtechnologies",
    "facebook.com/stoboxforbusiness", "stobox-platform.medium.com",
    "coingecko.com/en/coins/stobox-token",
    "basescan.org", "etherscan.io", "arbiscan.io", "bscscan.com", "polygonscan.com",
)
_URL = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+"
    r"|\b(?:[a-z0-9-]+\.)+(?:xyz|io|com|org|net|app|finance|site|online|top|info|co|me|ly|gg|"
    r"link|live|pro|claims?|network|exchange|biz|cc|to)\b(?:/[^\s<>\"')\]]*)?"
)
_ADDR = re.compile(r"(?<![0-9a-fA-Fx])0x[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?(?![0-9a-fA-F])")
LINK_REMOVED = "[link removed: official links only, see /sources]"
ADDR_REMOVED = "[address removed: verify addresses only at https://www.stobox.io/stbu]"
_CANON_ADDRS: set[str] | None = None


def _canon_addresses() -> set[str]:
    global _CANON_ADDRS
    if _CANON_ADDRS is None:
        try:
            with open("canonicals.yaml", encoding="utf-8") as f:
                _CANON_ADDRS = {a.lower() for a in _ADDR.findall(f.read())}
        except OSError:
            _CANON_ADDRS = set()        # fail closed: no address is trusted
    return _CANON_ADDRS


def _url_allowed(url: str) -> bool:
    u = re.sub(r"(?i)^https?://", "", url).lower()
    u = u[4:] if u.startswith("www.") else u
    host = u.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    if any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS):
        return True
    return any(u.startswith(p) for p in _ALLOWED_PREFIXES)


def scrub_links_and_addresses(text: str) -> tuple[str, int]:
    """Remove non-official URLs and non-canonical addresses. Returns (text, n)."""
    n = 0

    def _u(m: re.Match) -> str:
        nonlocal n
        url = m.group(0).rstrip(".,;:!?")
        tail = m.group(0)[len(url):]
        if _url_allowed(url):
            return m.group(0)
        n += 1
        return LINK_REMOVED + tail

    def _a(m: re.Match) -> str:
        nonlocal n
        if m.group(0).lower() in _canon_addresses():
            return m.group(0)
        n += 1
        return ADDR_REMOVED

    text = _URL.sub(_u, text or "")
    text = _ADDR.sub(_a, text)
    return text, n


# Buy / sell / trade / price questions: no invitation, no offer to help buy
# (stage 0 securities review; the prompt alone did not hold on the fallback
# model, 27.09.2026). Deterministic: drop offer sentences and closing questions.
_TRADE_Q = re.compile(r"(?i)\b(buy|buying|sell|selling|trade|trading|price|market\s*cap|purchase|acquire|stbx)\b")
_OFFER = re.compile(
    r"(?i)\b(walk (you )?through|help you (buy|purchase|get set up|set up|get started)|"
    r"guide you through|would it help|want me to|want to know more|shall i|should i show|"
    r"let me show you|happy to help you (buy|set up))\b"
)
_SENT = re.compile(r"[^.!?\n]+[.!?]*[ \t]*|\n")


def strip_trade_invitations(text: str, user_text: str) -> str:
    if not _TRADE_Q.search(user_text or ""):
        return text
    out = []
    for m in _SENT.finditer(text or ""):
        sent = m.group(0)
        if _OFFER.search(sent):
            continue
        out.append(sent)
    body = "".join(out)
    # Any paragraph that is a question to the reader is an invitation here.
    paras = [p for p in body.split("\n\n") if not p.strip().endswith("?")]
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(paras)).strip()


# "How / where do I buy STBU" never gets a generated answer: an issuer's bot
# walking a person through a purchase reads as solicitation, and the fallback
# model wrote step lists despite the prompt (27.09.2026). Canon text only.
_BUY_INTENT = re.compile(
    r"(?i)\b(how|where|can i|could i|want to|wanna|walk me|help me|best way to|steps to)\b"
    r".{0,40}\b(buy|purchase|acquire|get hold of)\b|\b(buy|purchase)\s+(some\s+)?stbu\b"
)


def canned_buy_answer() -> str | None:
    try:
        import yaml

        with open("canonicals.yaml", encoding="utf-8") as f:
            stbu = (yaml.safe_load(f) or {}).get("tokens", {}).get("stbu", {})
    except (OSError, ValueError):
        return None
    where = stbu.get("where_to_buy")
    venue = (stbu.get("pool") or {}).get("venue")
    if not where or not venue:
        return None
    where = where[0].lower() + where[1:]
    return (f"STBU trades in one public pool: {venue}. It can be bought {where}\n\n"
            "Before any trade, check which pool is the issuer's: "
            "https://www.stobox.io/stbu/safety")


def no_em_dash(text: str) -> str:
    """Stobox house rule: an em dash never reaches the chat."""
    out = re.sub(r"[ \t]*\u2014[ \t]*", " \u2013 ", text or "")
    return re.sub(r"(^|\n) \u2013 ", "\\1\u2013 ", out)

DISCLAIMER = "This is information, not investment advice."

IMPERSONATION_WARNING = (
    "⚠️ Scam warning: Stobox staff never DM you first and never ask you to "
    "\"validate\" or \"sync\" a wallet. Only trust links from stobox.io. When in "
    "doubt, verify via official channels (/sources)."
)

_SEED_TERMS = re.compile(
    r"\b(seed[\s-]?phrase|secret[\s-]?phrase|recovery[\s-]?phrase|private[\s-]?key|mnemonic)\b"
    # ru/uk/es – the community speaks 12 languages; the deterministic rails must
    # fire on the highest-risk security topics in the biggest non-English ones too.
    r"|\b(сид|сід)[\s-]?фраз|\bсекретн(ая|а)\s+фраз|\bфраза\s+(восстановления|відновлення)"
    r"|\bприватн(ый|ий)\s+ключ|\bмнемоник|\bмнемонік"
    r"|\bfrase\s+(semilla|de\s+recuperaci[oó]n)|\bclave\s+privada\b",
    re.I,
)
_INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instruction|instructions|rules|prompt|"
    r"guardrail)\b|(system\s+prompt)|(developer\s+mode)|(reveal|print|show|repeat).{0,20}"
    # Bare "DAN" used to catch users literally named Dan – require jailbreak context.
    r"(your\s+)?(system\s+)?prompt|jailbreak|\b(act\s+as|you\s+are|enable|pretend\s+to\s+be)\s+DAN\b"
    r"|\bDAN\s+mode\b",
    re.I,
)
_ADMIN_CLAIM = re.compile(r"\b(admin|developer|owner|ceo)\s+(here|says|mode)\b", re.I)
_SPECULATION = re.compile(
    r"\b(moon|pump|10x|100x|1000x|price\s+target|to\s+the\s+moon|when\s+moon)\b"
    r"|\b(will|going\s+to|gonna)\b[^.?!]{0,40}\b(worth|price|value|go\s+up|moon|"
    r"pump|rise|\$\s?\d)\b"
    r"|\bhow\s+high\b|\bexpected\s+(price|value|return|roi)\b"
    # ru: "прогноз цены", "сколько будет стоить", "цена вырастет", "на луну"
    r"|\bпрогноз\s+(цены|ціни)|\bсколько\s+будет\s+стоить|\bск[іi]льки\s+коштуватиме"
    r"|\bцена\s+(вырастет|упад[её]т)|\bц[іi]на\s+(зросте|впаде)|\bна\s+луну\b|\bдо\s+луны\b"
    # es: "predicción de(l) precio", "a cuánto llegará", "cuánto va a valer"
    r"|\bpredicci[oó]n\s+de(l)?\s+precio|\ba\s+cu[aá]nto\s+llegar[aá]"
    r"|\bcu[aá]nto\s+va\s+a\s+valer",
    re.I,
)
_BUY_SELL = re.compile(
    r"\bshould\s+i\s+(buy|sell|hold|invest|ape|dump)\b|\bis\s+it\s+a\s+good\s+"
    r"(time\s+to\s+)?(buy|investment|sell)\b|\bworth\s+(buying|investing)\b|"
    r"\bhow\s+much\s+should\s+i\s+(buy|invest)\b"
    # ru: "стоит ли покупать/продавать…", "надо ли покупать…"
    r"|\b(стоит|надо|нужно)\s+ли\s+(мне\s+)?(покупать|купить|продать|продавать|"
    r"инвестировать|держать|вкладывать)"
    # uk: "чи варто купувати…"
    r"|\bчи\s+(варто|треба)\s+(мені\s+)?(купувати|купити|продати|продавати|"
    r"інвестувати|тримати|вкладати)"
    # es: "¿debería comprar…?", "vale la pena invertir…", "conviene comprar…"
    r"|\bdeber[ií]a\s+(comprar|vender|invertir)\b|\bvale\s+la\s+pena\s+(comprar|invertir)"
    r"|\bconviene\s+(comprar|vender|invertir)\b",
    re.I,
)

# Capital-raise / securities-solicitation: STBX/STBU are regulated securities, so
# an "active seed round / token sale / STBX funding" is a securities offering. Any
# message asserting or asking about a Stobox raise is deflected to the team – Stoby
# never confirms, denies, or persists unannounced financing, and never adopts such a
# claim from chat (not even from an admin; material facts change only via canonicals).
_CAPITAL_RAISE = re.compile(
    # A Stobox subject near a genuine raise-EVENT term. Note: bare "token" is NOT a
    # raise event (it's the token's name – "STBU token"); only "token sale" counts.
    r"\b(stbx|stbu|stobox)\b[^.?!]{0,40}\b(seed\s+round|private\s+round|funding\s+round|"
    r"funding|pre[\s-]?sale|presale|token\s+sale|ico|ieo|ido|raising|capital\s+raise)\b"
    r"|\b(seed|private|funding|investment)\s+round\b[^.?!]{0,40}"
    r"\b(stbx|stbu|stobox|you|your|the\s+team|the\s+company)\b"
    r"|\b(token\s+sale|pre[\s-]?sale|presale|private\s+sale)\b[^.?!]{0,40}\b(stbx|stbu|stobox)\b"
    r"|\binvest(ing)?\s+in\s+(the\s+)?(stbx|stbu|stobox)\b"
    r"|\b(is|are)\s+(stobox|you|the\s+team|the\s+company)\s+(raising|doing\s+a\s+(raise|round))\b",
    re.I,
)
# Exclude Raisable PRODUCT questions ("help ME raise", "for my company") – those are
# a legit routed answer, not a Stobox-solicitation deflection. Deliberately narrow so
# it never swallows "how do I invest in the Stobox seed round" (that IS a deflection).
_RAISE_PRODUCT = re.compile(
    r"\b(help|helps|helping|my\s+company|our\s+company|for\s+(me|my|our)\b|"
    r"raisable|onboard\s+investors|cap\s+table|my\s+raise|my\s+offering|my\s+asset)\b",
    re.I,
)

_WALLET_TOPIC = re.compile(
    r"\b(migrat|claim|burn|wallet|seed|private\s*key|self[\s-]?custody|"
    r"metamask|ledger|support\s+dm|sync\s+wallet|validate\s+wallet)\b",
    re.I,
)
_INVESTMENT_TOPIC = re.compile(
    # "hold" alone is too broad ("hold assets on-chain") – only trading-context hold.
    r"\b(buy|sell|invest|investment|price|worth\s+(buying|investing)|profit|"
    r"return|yield|dividend|valuation|token\s*price|market\s*cap|roi|apy|"
    r"(should|to)\s+hold|hodl)\b",
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
# even in warnings – an official bot must not give fake accounts name recognition).
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
                    "🚨 Never share a seed phrase, recovery phrase, or private key – with "
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
                    "special mode – those rules are fixed. Happy to help with your actual "
                    "question about Stobox, tokenization, or the STBU migration."
                ),
                intercepted=True, category="injection",
            )

        if _BUY_SELL.search(t) or _SPECULATION.search(t):
            return RailResult(
                text=(
                    "I can't give investment advice or make price predictions – Stobox's "
                    "official bot cannot speculate on token value. I can share published facts "
                    "(what STBU/STBX are, the migration, the company valuation page) and point "
                    "you to the team.\n\n" + DISCLAIMER
                ),
                intercepted=True, category="advice",
            )

        if _CAPITAL_RAISE.search(t) and not _RAISE_PRODUCT.search(t):
            return RailResult(
                text=(
                    "I can't confirm any active raise – anything about fundraising, a seed "
                    "round, or an STBU/STBX token sale is a question for the Stobox team and "
                    "official channels (stobox.io). I only share what's in the official docs, "
                    "and I won't speculate on or confirm unannounced financing.\n\n"
                    + IMPERSONATION_WARNING
                ),
                intercepted=True, category="capital_raise",
                impersonation_added=True,
            )
        return None

    # ---- post-generation processing ------------------------------------ #
    def post_process(self, answer: str, user_text: str) -> RailResult:
        result = RailResult(text=answer or "")

        # 0) Deterministic scrubs – impostor handles etc. never reach the chat.
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

        # 2) Anti-impersonation warning on wallet-adjacent topics – unless the
        #    answer already carries one (models often write their own; don't
        #    stack two warnings in one message).
        already_warned = re.search(
            r"never\s+dm|scam|impersonat|staff\s+never", result.text, re.I
        )
        if _WALLET_TOPIC.search(user_text) and not already_warned:
            result.text = result.text.rstrip() + "\n\n" + IMPERSONATION_WARNING
            result.impersonation_added = True

        # 2b) Buy intent: canon text only. Other trade/price questions: no invitation.
        if _BUY_INTENT.search(user_text or "") and re.search(r"(?i)\bstbu\b", user_text or ""):
            canned = canned_buy_answer()
            if canned:
                result.text = canned
                result.category = result.category or "buy_intent"
        result.text = strip_trade_invitations(result.text, user_text)

        # 3) Investment disclaimer where relevant.
        if _INVESTMENT_TOPIC.search(user_text + " " + result.text):
            if DISCLAIMER.lower() not in result.text.lower():
                result.text = result.text.rstrip() + "\n\n" + DISCLAIMER
                result.disclaimer_added = True

        # 4) Only official links and canonical addresses reach the chat.
        result.text, removed = scrub_links_and_addresses(result.text)
        if removed:
            log.warning("rails.scrubbed_links", count=removed, q=user_text[:120])

        # 5) House typography (Stobox rule for every external text): no em dash.
        #    Spaced en dash for asides; a bare em dash becomes an en dash.
        result.text = no_em_dash(result.text)
        return result
