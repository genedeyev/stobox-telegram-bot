"""Moderation: cheap heuristics first, LLM classifier second.

Heuristics catch the obvious, high-precision cases (flood, seed-phrase phishing,
known scam patterns) without an LLM call. The LLM classifier handles nuance
(FUD vs honest criticism, subtle scams). Config maps each category to an action
on an escalation ladder: warn → delete → mute → ban.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from ..config import Config
from ..core.types import IncomingMessage, ModerationAction
from ..llm.base import ChatMessage, LLMProvider
from ..logging import get_logger
from ..prompts import get_prompts
from ..util import extract_json

log = get_logger(__name__)

# High-precision phishing/scam signals — do not require an LLM.
_SCAM_PATTERNS = [
    re.compile(r"\bseed\s*phrase\b", re.I),
    re.compile(r"\bprivate\s*key\b", re.I),
    re.compile(r"\b(recovery|secret)\s*phrase\b", re.I),
    re.compile(r"\bwallet\s*(connect|validation|sync)\b.*\b(http|www|\.io|\.com)\b", re.I),
    re.compile(r"dm\s+me.*(recover|unlock|support|admin)", re.I),
    re.compile(r"(claim|airdrop).*(connect|verify).*(wallet)", re.I),
]
_LEVEL_SENSITIVITY = {"off": 2.0, "light": 0.85, "standard": 0.6, "strict": 0.4}


@dataclass(slots=True)
class ModerationVerdict:
    action: ModerationAction = ModerationAction.NONE
    category: str | None = None
    score: float = 0.0
    reason: str = ""
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def flagged(self) -> bool:
        return self.action != ModerationAction.NONE


class Moderator:
    def __init__(self, config: Config, classifier: LLMProvider) -> None:
        self.config = config
        self.classifier = classifier
        m = config.section("moderation")
        self.enabled = bool(m.get("enabled", True))
        self.level = m.get("level", "standard")
        self.threshold = _LEVEL_SENSITIVITY.get(self.level, 0.6)
        self.actions: dict[str, str] = m.get("actions", {}) or {}
        flood = m.get("flood", {}) or {}
        self.flood_max = int(flood.get("max_messages", 5))
        self.flood_secs = int(flood.get("per_seconds", 10))
        self._recent: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.flood_max + 1))
        self.prompts = get_prompts()

    async def evaluate(self, msg: IncomingMessage) -> ModerationVerdict:
        if not self.enabled or self.level == "off" or msg.author.is_admin:
            return ModerationVerdict()

        # 1) Flood (heuristic, no LLM).
        if self._is_flood(msg.author.external_id):
            return self._verdict("flood", 1.0, "message flood")

        # 2) High-precision scam/phishing patterns (heuristic).
        for pat in _SCAM_PATTERNS:
            if pat.search(msg.text):
                return self._verdict("scam", 0.98, f"pattern:{pat.pattern[:30]}")

        # 3) LLM classifier for nuance.
        scores = await self._classify(msg.text)
        if scores:
            category, score = max(scores.items(), key=lambda kv: kv[1])
            if score >= self.threshold and category in self.actions:
                return self._verdict(category, score, scores.get("reason", ""), scores)
        return ModerationVerdict(scores=scores)

    def _is_flood(self, user_id: str) -> bool:
        now = time.monotonic()
        dq = self._recent[user_id]
        dq.append(now)
        recent = [t for t in dq if now - t <= self.flood_secs]
        return len(recent) > self.flood_max

    async def _classify(self, text: str) -> dict[str, float]:
        if len(text.strip()) < 3:
            return {}
        prompt = self.prompts.render("moderation", text=text[:1500])
        try:
            raw = await self.classifier.complete_json(
                [ChatMessage("user", prompt)], max_tokens=160
            )
            data = extract_json(raw)
            if not isinstance(data, dict):
                return {}
            return {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in data.items()}
        except Exception as exc:  # noqa: BLE001 - moderation must never crash the flow
            log.warning("moderation.classify_failed", error=str(exc))
            return {}

    def _verdict(self, category: str, score: float, reason: str, scores=None) -> ModerationVerdict:
        action = ModerationAction(self.actions.get(category, "warn"))
        log.info("moderation.flag", category=category, action=action.value, score=round(score, 2))
        return ModerationVerdict(
            action=action, category=category, score=score, reason=reason, scores=scores or {}
        )
