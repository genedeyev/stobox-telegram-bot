"""Moderation: deterministic filters first, LLM classifier second, then the
progressive-discipline policy.

Pipeline per message (groups, non-admins only):
  1. impersonation check (display name vs protected terms) — highest priority;
  2. deterministic filters (flood, hard slurs, doxxing, scam/phishing patterns);
  3. LLM classifier for nuance (harassment vs banter, FUD vs honest criticism);
  4. the strike-aware policy maps (category, active-strike-count) → graded action.

Casual profanity is NOT policed — only targeting, hate, scams, doxxing. Honest
criticism falls through untouched.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.types import Author, IncomingMessage, ModerationAction
from ..llm.base import ChatMessage, LLMProvider
from ..logging import get_logger
from ..prompts import get_prompts
from ..util import extract_json
from . import policy as pol
from .strikes import StrikeBook

log = get_logger(__name__)

# High-precision phishing/scam signals — no LLM needed.
_SCAM_PATTERNS = [
    re.compile(r"\bseed\s*phrase\b", re.I),
    re.compile(r"\bprivate\s*key\b", re.I),
    re.compile(r"\b(recovery|secret)\s*phrase\b", re.I),
    re.compile(r"\bwallet\s*(connect|validation|sync)\b.*\b(http|www|\.io|\.com)\b", re.I),
    re.compile(r"dm\s+me.*(recover|unlock|support|admin|help)", re.I),
    re.compile(r"(claim|airdrop).*(connect|verify).*(wallet)", re.I),
    re.compile(r"(text|message|contact|reach out to).{0,20}(support|admin).{0,20}(telegram|whatsapp|t\.me)", re.I),
]
_DM_BAIT = re.compile(r"\bdm\s+me\b|\bmessage\s+me\b|\bcontact\s+(me|support|admin)\b|t\.me/\S+", re.I)
_LINK = re.compile(r"https?://|t\.me/|www\.", re.I)

# Doxxing: personal data posted about others (conservative — needs a directive).
_DOXX = re.compile(
    r"\b(his|her|their|this\s+guy'?s?|@\w+'?s?)\s+(phone|number|address|home|"
    r"email|real\s+name|passport|id)\b|"
    r"\b(phone|address|home)\s*(is|:)\s*[\+\d].{4,}",
    re.I,
)

# Hard, unambiguous slurs (deterministic hate layer). Kept minimal; the LLM
# classifier catches nuance and other languages. Admins extend the blocklist
# via moderation.blocklist_path (a file of one term per line, not committed).
_HARD_SLURS_SEED = [
    r"n[i1]gg[e3]r", r"n[i1]gg[a4]", r"f[a4]gg?[o0]t", r"f[a4]g\b",
    r"k[i1]k[e3]", r"tr[a4]nn[y]", r"ch[i1]nk", r"sp[i1]c\b", r"r[e3]t[a4]rd",
]

_LEVEL_SENSITIVITY = {"off": 2.0, "light": 0.85, "standard": 0.6, "strict": 0.4}
# Default protected identity terms — display names containing these, from a
# non-allowlisted account, are treated as impersonation risks.
_PROTECTED_TERMS = ["stobox", "support", "admin", "moderator", "official", "team"]


@dataclass(slots=True)
class ModerationVerdict:
    action: ModerationAction = ModerationAction.NONE
    category: str | None = None
    score: float = 0.0
    reason: str = ""
    delete: bool = False
    mute_minutes: int = 0
    strike_count: int = 0
    alert_admin: bool = False
    warn_text: str = ""          # posted publicly (WARN only)
    dm_text: str = ""            # sent privately to the offender
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def flagged(self) -> bool:
        return self.action != ModerationAction.NONE or self.alert_admin


class Moderator:
    def __init__(self, config: Config, classifier: LLMProvider, strikes: StrikeBook) -> None:
        self.config = config
        self.classifier = classifier
        self.strikes = strikes
        m = config.section("moderation")
        self.enabled = bool(m.get("enabled", True))
        # Coexist mode: ChatKeeperBot is the moderator. When enforce is False,
        # Stoby DETECTS but never deletes/bans/mutes — it only flags active
        # scams/impersonation to admins. No message of anyone's is removed.
        self.enforce = bool(m.get("enforce", True))
        self.level = m.get("level", "standard")
        self.threshold = _LEVEL_SENSITIVITY.get(self.level, 0.6)
        flood = m.get("flood", {}) or {}
        self.flood_max = int(flood.get("max_messages", 5))
        self.flood_secs = int(flood.get("per_seconds", 10))
        self._recent: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.flood_max + 1))
        self.prompts = get_prompts()
        self.announce_actions = bool(m.get("announce_actions", False))
        self.protected = [t.lower() for t in (m.get("protected_terms") or _PROTECTED_TERMS)]
        # Team names to protect from impersonation (config-driven).
        self.team_names = [t.lower() for t in (m.get("team_names") or [])]
        self.allowlist = {str(x) for x in (m.get("impersonation_allowlist") or [])}
        self._slur_re = self._build_slur_re(m.get("blocklist_path"))
        # Link allowlist: only official Stobox links survive from a non-admin;
        # every other link is removed (admins are exempt — see evaluate()).
        from .links import LinkPolicy

        lp = m.section("link_policy")
        self.link_policy_enabled = bool(lp.get("enabled", True))
        self.links = LinkPolicy(lp.get("allow") or [])

    def _build_slur_re(self, blocklist_path):
        terms = list(_HARD_SLURS_SEED)
        if blocklist_path and Path(blocklist_path).exists():
            try:
                extra = [ln.strip() for ln in Path(blocklist_path).read_text().splitlines()
                         if ln.strip() and not ln.startswith("#")]
                terms += [re.escape(t) for t in extra]
                log.info("moderation.blocklist_loaded", terms=len(extra))
            except Exception as exc:  # noqa: BLE001
                log.warning("moderation.blocklist_failed", error=str(exc))
        return re.compile(r"(?<![a-z0-9])(" + "|".join(terms) + r")(?![a-z0-9])", re.I)

    # ------------------------------------------------------------------ #
    async def evaluate(self, msg: IncomingMessage) -> ModerationVerdict:
        if not self.enabled or self.level == "off" or msg.author.is_admin:
            return ModerationVerdict()
        text = msg.text or ""
        user_key = f"{msg.channel}:{msg.author.external_id}"

        # 1) Impersonation (identity-based).
        imp = self._impersonation(msg.author)
        if imp:
            scammy = bool(_DM_BAIT.search(text) or _LINK.search(text)
                          or any(p.search(text) for p in _SCAM_PATTERNS))
            if scammy:  # impersonator actively baiting → treat as scam.
                return self._sanction(user_key, "scam", 0.99, msg, "impersonator baiting")
            # Benign-looking impersonator name → alert admins, let them decide.
            return ModerationVerdict(
                category="impersonation", score=0.9, alert_admin=True,
                reason=f"display name '{msg.author.display_name}' mimics Stobox",
                dm_text="", scores={"impersonation": 0.9},
            )

        # 2) Deterministic filters.
        if self._is_flood(msg.author.external_id):
            return self._sanction(user_key, "flood", 1.0, msg, "message flood")
        if self._slur_re.search(text):
            return self._sanction(user_key, "hate_slur", 0.98, msg, "hard slur match")
        if _DOXX.search(text):
            return self._sanction(user_key, "doxxing", 0.9, msg, "doxxing pattern")
        for pat in _SCAM_PATTERNS:
            if pat.search(text):
                return self._sanction(user_key, "scam", 0.98, msg, f"pattern:{pat.pattern[:24]}")

        # 2b) Link allowlist — only official Stobox links survive from a non-admin.
        # Admins already returned at the top of evaluate(), so any link here is
        # from a regular user. Runs after scam patterns so a phishing link bans
        # rather than merely deletes.
        if self.link_policy_enabled:
            bad = self.links.disallowed(text)
            if bad:
                return self._sanction(user_key, "external_link", 0.95, msg,
                                      f"non-official link: {bad[0][:60]}")

        # 3) LLM classifier for nuance.
        scores = await self._classify(text)
        if scores:
            cat, score = self._top(scores)
            if cat and score >= self.threshold and cat not in pol.SAFE_CATEGORIES:
                return self._sanction(user_key, cat, score, msg, scores.get("reason", ""), scores)
        return ModerationVerdict(scores=scores)

    # ------------------------------------------------------------------ #
    def _impersonation(self, author: Author) -> bool:
        if str(author.external_id) in self.allowlist:
            return False
        name = f"{author.display_name or ''} {author.username or ''}".lower()
        if any(t in name for t in self.protected):
            return True
        return any(tn and tn in name for tn in self.team_names)

    def _is_flood(self, user_id: str) -> bool:
        now = time.monotonic()
        dq = self._recent[user_id]
        dq.append(now)
        return len([t for t in dq if now - t <= self.flood_secs]) > self.flood_max

    @staticmethod
    def _top(scores: dict[str, float]) -> tuple[str | None, float]:
        numeric = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        if not numeric:
            return None, 0.0
        cat = max(numeric, key=numeric.get)
        return cat, float(numeric[cat])

    async def _classify(self, text: str) -> dict:
        if len(text.strip()) < 3:
            return {}
        prompt = self.prompts.render("moderation", text=text[:1500])
        try:
            raw = await self.classifier.complete_json([ChatMessage("user", prompt)], max_tokens=200)
            data = extract_json(raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001 - moderation must never crash the flow
            log.warning("moderation.classify_failed", error=str(exc))
            return {}

    def _sanction(self, user_key: str, category: str, score: float,
                  msg: IncomingMessage, reason: str, scores=None) -> ModerationVerdict:
        # Coexist mode: don't act — ChatKeeper enforces. Only flag an ACTIVE
        # scam/impersonation to admins (a heads-up, not a punishment); nobody's
        # message is deleted, muted, or banned by Stoby.
        if not self.enforce:
            alert = category in ("scam", "phishing", "impersonation")
            if not alert:
                return ModerationVerdict(scores=scores or {})   # NONE, no action
            return ModerationVerdict(
                category=category, score=score, alert_admin=True,
                reason=reason or pol.reason_text(category), scores=scores or {},
            )
        count = self.strikes.add(
            user_key, category, display_name=msg.author.display_name or "",
            chat_id=msg.chat_id, excerpt=(msg.text or "")[:160],
        )
        step = pol.decide(category, count)
        if step.action == ModerationAction.BAN:
            self.strikes.set_banned(user_key, True, msg.author.display_name or "")
        human = pol.reason_text(category)
        warn_text = ""
        if step.action == ModerationAction.WARN:
            warn_text = (
                "⚠️ Let's keep it constructive and respectful. "
                "Repeated issues can lead to a mute."
            )
        if category == "external_link":
            # Soft, non-punitive: this is hygiene, not a strike-shaming.
            dm_text = (
                "🗑 Heads-up: your message was removed because this group only allows "
                "official Stobox links (anything on stobox.io). It's a scam-prevention "
                "rule — nothing personal. Feel free to repost your point without the "
                "outside link, and reply /appeal if you think this was a mistake."
            )
        else:
            dm_text = self._dm(step.action, human, count, step.mute_minutes)
        log.info("moderation.sanction", category=category, action=step.action.value,
                 strike=count, user=user_key, score=round(score, 2))
        return ModerationVerdict(
            action=step.action, category=category, score=score, reason=reason or human,
            delete=step.delete, mute_minutes=step.mute_minutes, strike_count=count,
            alert_admin=(category in pol.ALERT_CATEGORIES or step.action == ModerationAction.BAN),
            warn_text=warn_text, dm_text=dm_text, scores=scores or {},
        )

    @staticmethod
    def _dm(action: ModerationAction, reason: str, strike: int, mute_min: int) -> str:
        head = {
            ModerationAction.WARN: "⚠️ A quick heads-up about a message in the Stobox community",
            ModerationAction.DELETE: "🗑 A message of yours in the Stobox community was removed",
            ModerationAction.MUTE: f"🔇 You've been muted in the Stobox community for {mute_min//60}h"
                                   if mute_min >= 60 else f"🔇 You've been muted for {mute_min} min",
            ModerationAction.BAN: "⛔ You've been removed from the Stobox community",
        }.get(action, "")
        if not head:
            return ""
        return (
            f"{head} — reason: {reason} (strike {strike}). "
            "Our community stays constructive and scam-free. If you believe this was a "
            "mistake, reply /appeal and a human admin will review it."
        )
