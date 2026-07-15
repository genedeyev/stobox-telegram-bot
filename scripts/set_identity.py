"""Set Stoby's public Telegram identity via the Bot API (self-configuration).

Uses the bot's own token (from .env) to set the display name, descriptions, and
the command menu — the same thing BotFather's /setname etc. do, but scripted and
repeatable. Safe to run while the bot is live (these are stateless config calls,
unlike getUpdates). Telegram rate-limits name changes, so don't spam it.

    python scripts/set_identity.py
"""

from __future__ import annotations

import asyncio
import os
import sys

NAME = "Stoby | AI Assistant"
DESCRIPTION = (
    "Stoby — the resident AI of the Stobox community. "
    "Part monster, part mind, fully awake. Ask me anything about tokenization."
)
SHORT_DESCRIPTION = (   # ≤120 chars (shown on the profile / empty-chat screen)
    "Resident AI of the Stobox community. Part monster, part mind, fully awake. "
    "Ask me anything about tokenization."
)
COMMANDS = [
    ("guide", "What Stoby can do (quick tour)"),
    ("qualify", "Quick tokenization fit check (30s)"),
    ("migrate", "STBU → Base migration"),
    ("check", "Check your STBU across chains"),
    ("compass", "Stobox Compass + readiness check"),
    ("valuation", "Company valuation"),
    ("blog", "Latest posts + RWA digest"),
    ("rank", "Your XP, level & streak"),
    ("leaderboard", "Top community members"),
    ("ama", "Submit a question for the AMA"),
    ("subscribe", "Pick topics to get DM'd about"),
    ("remindme", "Migration deadline reminders"),
    ("sources", "Official links to verify me"),
    ("contact", "Reach the team"),
    ("help", "What Stoby can do"),
]


async def main() -> None:
    from stobox_ai.preflight import load_dotenv_if_present

    load_dotenv_if_present()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not set (check .env).", file=sys.stderr)
        raise SystemExit(1)

    import httpx

    base = f"https://api.telegram.org/bot{token}"
    calls = {
        "setMyName": {"name": NAME},
        "setMyDescription": {"description": DESCRIPTION[:512]},
        "setMyShortDescription": {"short_description": SHORT_DESCRIPTION[:120]},
        "setMyCommands": {"commands": [{"command": c, "description": d} for c, d in COMMANDS]},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        for method, payload in calls.items():
            r = await client.post(f"{base}/{method}", json=payload)
            js = r.json()
            ok = js.get("ok")
            detail = "" if ok else f" — {js.get('description')}"
            print(f"{'✅' if ok else '❌'} {method}{detail}")


if __name__ == "__main__":
    asyncio.run(main())
