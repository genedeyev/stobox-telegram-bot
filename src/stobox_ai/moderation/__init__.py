"""Moderation subsystem: spam/scam/FUD/toxicity/flood detection + action ladder."""

from .detector import ModerationVerdict, Moderator

__all__ = ["Moderator", "ModerationVerdict"]
