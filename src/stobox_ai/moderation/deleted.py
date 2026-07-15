"""Detecting Telegram "deleted accounts" for group hygiene.

When a person deletes their Telegram account, their membership lingers in every
group as a ghost "Deleted Account" — empty name, no username, never posts. Real
accounts always carry a non-empty first_name, so the absence of one (together
with no username and no last name, and not being a bot) is a reliable signal.

We keep the check conservative on purpose: removing a member is destructive, so
we only ever flag the unmistakable ghost shape and never a real user who merely
lacks a username.
"""

from __future__ import annotations

from typing import Protocol


class _UserLike(Protocol):
    first_name: str | None
    last_name: str | None
    username: str | None
    is_bot: bool


def is_deleted_account(user: _UserLike | None) -> bool:
    """True only for the unmistakable deleted-account shape.

    Telegram requires a non-empty ``first_name`` for every live human account, so
    an empty one — with no username and no last name, and not a bot — means the
    account was deleted. Bots and any user with a name or handle are never
    flagged.
    """
    if user is None or getattr(user, "is_bot", False):
        return False
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    username = (getattr(user, "username", None) or "").strip()
    return not first and not last and not username
