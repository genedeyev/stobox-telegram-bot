"""Intent router — one cheap classifier call that decides how to handle a
message: mode, persona, language, buying intent, whether docs are needed.

Falls back to fast heuristics (langdetect + keyword rules) if the classifier is
unavailable, so routing never hard-fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.types import Mode
from ..llm.base import ChatMessage, LLMProvider
from ..logging import get_logger
from ..prompts import get_prompts
from ..util import extract_json

log = get_logger(__name__)

_SUPPORTED_LANGS = {"en", "ru", "uk", "es", "fr", "de", "ar", "zh", "ja", "pt", "it", "ro"}
_BUY_HINTS = re.compile(
    r"\b(price|pricing|cost|demo|onboard|get started|sign up|buy|purchase|quote|"
    r"tokenize my|our (asset|fund|company)|how much|talk to sales)\b",
    re.I,
)
_QUESTION = re.compile(r"\?|\b(how|what|why|when|where|which|can|does|is|are|do)\b", re.I)
# Cheap emotional-temperature markers for the heuristic fallback (the LLM router
# gives finer labels). Order matters: FUD/anger checked before generic negativity.
_FUD = re.compile(
    r"\b(scam|fraud|ponzi|rug\s?pull|rugpull|exit\s?scam|dump(ing|ed)?|"
    r"dead\s?(coin|project)|honeypot|fake|shill)\b",
    re.I,
)
_ANGER = re.compile(
    r"\b(wtf|f[\*u]ck|bullshit|garbage|useless|trash|scammers?|liars?|"
    r"pissed|furious|hate|worst|ridiculous)\b|!!+",
    re.I,
)
_FRUSTRATION = re.compile(
    r"\b(still (not|don'?t|can'?t)|not working|broken|stuck|confused|"
    r"no (one|body) (answer|help)|been waiting|why is this so|frustrat)\b",
    re.I,
)

# Emotional states that call for a calm, de-escalating reply rather than a
# neutral doc answer. Engine reads this set (see _answer / _should_engage).
HOT_SENTIMENTS = {"frustrated", "angry", "anxious", "fud", "toxic"}
_SENTIMENTS = {"neutral", "positive"} | HOT_SENTIMENTS


@dataclass(slots=True)
class Routing:
    mode: Mode = Mode.COMMUNITY_MANAGER
    persona: str = "unknown"
    language: str = "en"
    technical_level: str = "unknown"     # beginner | intermediate | expert
    buying_intent: bool = False
    is_question: bool = False
    needs_docs: bool = False
    topics: list[str] = field(default_factory=list)
    # neutral | positive | frustrated | angry | anxious | fud | toxic
    sentiment: str = "neutral"


class IntentRouter:
    def __init__(self, classifier: LLMProvider) -> None:
        self.classifier = classifier
        self.prompts = get_prompts()

    async def route(self, text: str, reply_to: str | None = None) -> Routing:
        heuristic = self._heuristic(text)
        prompt = self.prompts.render("intent_router", text=text[:1500], reply_to=reply_to or "—")
        try:
            raw = await self.classifier.complete_json(
                [ChatMessage("user", prompt)], max_tokens=240
            )
            data = extract_json(raw)
            if not isinstance(data, dict):
                raise ValueError("router: no JSON in classifier output")
            lang = str(data.get("language", heuristic.language)).lower()[:2]
            sentiment = str(data.get("sentiment", heuristic.sentiment)).lower()
            if sentiment not in _SENTIMENTS:
                sentiment = heuristic.sentiment
            return Routing(
                mode=self._mode(data.get("mode")),
                persona=str(data.get("persona", "unknown")),
                language=lang if lang in _SUPPORTED_LANGS else heuristic.language,
                technical_level=str(data.get("technical_level", "unknown")),
                buying_intent=bool(data.get("buying_intent", heuristic.buying_intent)),
                is_question=bool(data.get("is_question", heuristic.is_question)),
                needs_docs=bool(data.get("needs_docs", heuristic.needs_docs)),
                topics=[str(t) for t in data.get("topics", [])][:6],
                sentiment=sentiment,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("router.fallback_heuristic", error=str(exc))
            return heuristic

    @staticmethod
    def _mode(value) -> Mode:
        try:
            return Mode(value)
        except (ValueError, TypeError):
            return Mode.COMMUNITY_MANAGER

    @staticmethod
    def _heuristic(text: str) -> Routing:
        lang = "en"
        try:
            from langdetect import detect

            code = detect(text)[:2]
            lang = code if code in _SUPPORTED_LANGS else "en"
        except Exception:  # noqa: BLE001
            pass
        buying = bool(_BUY_HINTS.search(text))
        is_q = bool(_QUESTION.search(text))
        if _FUD.search(text):
            sentiment = "fud"
        elif _ANGER.search(text):
            sentiment = "angry"
        elif _FRUSTRATION.search(text):
            sentiment = "frustrated"
        else:
            sentiment = "neutral"
        return Routing(
            mode=Mode.SALES_ASSISTANT if buying else Mode.COMMUNITY_MANAGER,
            language=lang,
            buying_intent=buying,
            is_question=is_q,
            needs_docs=is_q or buying,
            sentiment=sentiment,
        )
