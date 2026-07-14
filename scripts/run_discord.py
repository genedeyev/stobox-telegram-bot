"""Run the Discord channel over the shared engine.

    DISCORD_BOT_TOKEN=... python scripts/run_discord.py

Needs the discord extra: pip install -e ".[discord]". Enable the "Message
Content" privileged intent in the Discord developer portal.
"""

from __future__ import annotations

import asyncio

from stobox_ai.channels.discord import DiscordChannel
from stobox_ai.config import load_config
from stobox_ai.core.engine import AgentEngine
from stobox_ai.logging import configure_logging


async def main() -> None:
    configure_logging()
    engine = await AgentEngine.create(load_config())
    channel = DiscordChannel(engine)
    try:
        await channel.start()
    finally:
        await channel.stop()


if __name__ == "__main__":
    asyncio.run(main())
