"""Channel-agnostic domain types.

These are the lingua franca between channel adapters (Telegram today; Discord,
Slack, web widget tomorrow) and the reasoning core. A channel adapter's only
job is to translate its native events into ``IncomingMessage`` and render an
``AgentResponse`` back.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


class ChatType(str, enum.Enum):
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class Attachment(str, enum.Enum):
    IMAGE = "image"
    PDF = "pdf"
    DOCUMENT = "document"
    VOICE = "voice"
    VIDEO = "video"
    STICKER = "sticker"
    LINK = "link"


class Mode(str, enum.Enum):
    """High-level behaviour the orchestrator routes a message into."""
    COMMUNITY_MANAGER = "community_manager"
    TECHNICAL_EXPERT = "technical_expert"
    SALES_ASSISTANT = "sales_assistant"
    MODERATOR = "moderator"
    EVANGELIST = "product_evangelist"
    SMALL_TALK = "small_talk"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def from_score(cls, score: float, threshold: float) -> Confidence:
        if score < threshold:
            return cls.LOW
        if score < (threshold + 1.0) / 2:
            return cls.MEDIUM
        return cls.HIGH


@dataclass(slots=True)
class Author:
    external_id: str                 # channel-native user id (stringified)
    channel: str = "telegram"
    username: str | None = None
    display_name: str | None = None
    is_admin: bool = False


@dataclass(slots=True)
class IncomingMessage:
    """A normalized inbound message from any channel."""
    author: Author
    text: str
    chat_id: str
    chat_type: ChatType
    message_id: str
    channel: str = "telegram"
    thread_id: str | None = None          # Telegram topic / forum thread
    reply_to_text: str | None = None
    is_forwarded: bool = False
    is_edited: bool = False
    attachments: list[Attachment] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=_now)

    @property
    def is_private(self) -> bool:
        return self.chat_type == ChatType.PRIVATE


@dataclass(slots=True)
class Citation:
    """A reference backing a factual claim — the anti-hallucination anchor."""
    title: str
    section: str | None = None
    version: str | None = None
    source_file: str | None = None
    source_url: str | None = None

    def render(self) -> str:
        parts = [self.title]
        if self.version:
            parts.append(f"v{self.version}")
        if self.section:
            parts.append(f"Section {self.section}")
        label = ", ".join(parts)
        return f"{label} ({self.source_url})" if self.source_url else label


class ModerationAction(str, enum.Enum):
    NONE = "none"
    WARN = "warn"
    DELETE = "delete"
    MUTE = "mute"
    BAN = "ban"


@dataclass(slots=True)
class AgentResponse:
    """What the core hands back to a channel adapter to render."""
    text: str
    confidence: Confidence = Confidence.MEDIUM
    confidence_score: float = 0.0
    citations: list[Citation] = field(default_factory=list)
    mode: Mode = Mode.COMMUNITY_MANAGER
    persona: str = "auto"
    language: str = "en"
    moderation: ModerationAction = ModerationAction.NONE
    escalate: bool = False
    lead_captured: bool = False
    reply_to_message_id: str | None = None
    # Free-form structured metadata for the analytics/decision log.
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def should_reply(self) -> bool:
        return bool(self.text and self.text.strip())
