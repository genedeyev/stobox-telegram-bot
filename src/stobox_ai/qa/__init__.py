"""Unanswered-question loop.

When the bot can't answer (the IDK gate fires), the question is captured here,
mirrored as a DRAFT into the stobox-v15 Community QA register, and admins are
notified. An admin answers via `/answer <id> <text>`; the answer is written to
the register as APPROVED, appended to the local knowledge file (hot-reloaded by
the docs watcher), and delivered back to everyone who asked.
"""

from .register import QAEntry, QARegister

__all__ = ["QARegister", "QAEntry"]
