"""Channel adapters. Telegram today; the base contract lets Discord/Slack/web
plug into the same :class:`~stobox_ai.core.engine.AgentEngine`."""

from .base import Channel

__all__ = ["Channel"]
