"""Topic subscriptions (strictly opt-in, DM only).

Users pick topics they care about — migration, rwa-news, product — and Stoby
DMs them only when something in that lane ships (a matching blog post, a product
update, a migration milestone). Every push carries a one-tap way out, and we
only ever message people who explicitly opted in, so this respects the
never-initiate rule the same way /remindme does.

State persists to JSON: chat_id -> {topics: [...], language}. A blog post is
routed to a topic by keyword (see classify_topic); if it matches nothing, it is
only announced in-group and never DM-pushed.
"""

from __future__ import annotations

from pathlib import Path

from ..logging import get_logger

log = get_logger(__name__)

# Canonical topics the user can subscribe to. label/blurb drive the toggle UI.
TOPICS: dict[str, dict[str, str]] = {
    "migration": {
        "label": "🔁 STBU → Base migration",
        "blurb": "Deadlines, claim windows, and step-by-step migration news.",
    },
    "rwa-news": {
        "label": "📰 RWA & tokenization news",
        "blurb": "Industry moves, regulation, and real-world-asset trends.",
    },
    "product": {
        "label": "🧭 Product updates",
        "blurb": "New Compass features, releases, and platform announcements.",
    },
}

# Keyword hints per topic, checked against a post's title + teaser (lowercased).
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "migration": ("migrat", "stbu", "stbx", "burn", "claim", "swap to base"),
    "product": ("compass", "release", "launch", "feature", "update", "product",
                "platform", "axis", "dashboard"),
    "rwa-news": ("rwa", "real-world", "real world", "tokeniz", "regulat",
                 "security token", "sto ", "issuance", "market"),
}


def valid_topic(topic: str) -> bool:
    return topic in TOPICS


def classify_topic(title: str, teaser: str = "") -> str | None:
    """Best-effort single-topic routing for a blog post. Product/migration win
    over the broad rwa-news bucket when both match. Returns None if nothing fits.
    """
    hay = f"{title} {teaser}".lower()
    for topic in ("migration", "product", "rwa-news"):  # specific → general
        if any(kw in hay for kw in _TOPIC_KEYWORDS[topic]):
            return topic
    return None


class SubscriptionBook:
    def __init__(self, state_path: str | Path = "data/subscriptions.json") -> None:
        self.path = Path(state_path)
        # chat_id -> {"topics": [str], "language": str}
        self.subs: dict[str, dict] = {}
        self._load()

    # -- mutations ----------------------------------------------------- #
    def subscribe(self, chat_id: str, topic: str, language: str = "en") -> bool:
        """Add a topic. Returns True if it was newly added."""
        if not valid_topic(topic):
            return False
        rec = self.subs.setdefault(chat_id, {"topics": [], "language": language})
        rec["language"] = language
        if topic in rec["topics"]:
            return False
        rec["topics"].append(topic)
        self._save()
        log.info("subs.subscribed", chat=chat_id, topic=topic)
        return True

    def unsubscribe(self, chat_id: str, topic: str) -> bool:
        """Remove a topic. Returns True if it was present. Drops empty records."""
        rec = self.subs.get(chat_id)
        if not rec or topic not in rec["topics"]:
            return False
        rec["topics"].remove(topic)
        if not rec["topics"]:
            self.subs.pop(chat_id, None)
        self._save()
        log.info("subs.unsubscribed", chat=chat_id, topic=topic)
        return True

    def unsubscribe_all(self, chat_id: str) -> bool:
        existed = self.subs.pop(chat_id, None) is not None
        if existed:
            self._save()
        return existed

    def toggle(self, chat_id: str, topic: str, language: str = "en") -> bool:
        """Flip a topic on/off. Returns True if now subscribed, False if removed."""
        if topic in self.topics_for(chat_id):
            self.unsubscribe(chat_id, topic)
            return False
        self.subscribe(chat_id, topic, language)
        return True

    # -- queries ------------------------------------------------------- #
    def topics_for(self, chat_id: str) -> list[str]:
        rec = self.subs.get(chat_id)
        return list(rec["topics"]) if rec else []

    def subscribers_for(self, topic: str) -> list[tuple[str, str]]:
        """Return [(chat_id, language), ...] opted into this topic."""
        return [
            (cid, rec.get("language", "en"))
            for cid, rec in self.subs.items()
            if topic in rec.get("topics", [])
        ]

    # -- persistence --------------------------------------------------- #
    def _load(self) -> None:
        from .statefile import load_json_guarded

        data = load_json_guarded(self.path, label="subs")
        if data is None:
            return
        try:
            self.subs = {str(k): dict(v) for k, v in data.get("subs", {}).items()}
        except Exception as exc:  # noqa: BLE001
            log.error("subs.load_failed", error=str(exc))

    def _save(self) -> None:
        from .statefile import save_json_atomic

        try:
            save_json_atomic(self.path, {"subs": self.subs})
        except Exception as exc:  # noqa: BLE001
            log.error("subs.save_failed", error=str(exc))
