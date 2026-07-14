"""Memory data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ConversationTurn:
    role: str          # "user" | "assistant"
    text: str
    at: datetime = field(default_factory=_now)


@dataclass(slots=True)
class UserProfile:
    """Long-term per-user memory (spec: interests, technical level, language,
    products discussed, customer stage, previous questions, last interaction,
    lead score)."""
    user_key: str                                   # f"{channel}:{external_id}"
    display_name: str | None = None
    language: str = "en"
    technical_level: str = "unknown"                # beginner | intermediate | expert
    persona: str = "auto"                           # inferred persona
    interests: list[str] = field(default_factory=list)
    products_discussed: list[str] = field(default_factory=list)
    customer_stage: str = "member"                  # member | curious | evaluating | lead | customer
    recent_questions: list[str] = field(default_factory=list)
    lead_score: int = 0                             # 0..100
    helpful_answers: int = 0                        # drives the share-with-a-friend cadence
    email: str | None = None
    first_seen: datetime = field(default_factory=_now)
    last_interaction: datetime = field(default_factory=_now)
    notes: str = ""

    def touch(self) -> None:
        self.last_interaction = _now()

    def add_interest(self, item: str) -> None:
        item = item.strip().lower()
        if item and item not in self.interests:
            self.interests.append(item)
            self.interests = self.interests[-25:]

    def add_product(self, item: str) -> None:
        item = item.strip()
        if item and item not in self.products_discussed:
            self.products_discussed.append(item)

    def record_question(self, q: str) -> None:
        self.recent_questions.append(q.strip()[:300])
        self.recent_questions = self.recent_questions[-15:]
