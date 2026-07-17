"""Moderation severity matrix — progressive discipline.

Core principle: punish TARGETING, not vocabulary. Casual profanity is ignored;
abuse aimed at a person, hate speech, scams, and doxxing are sanctioned on an
escalating ladder keyed to the user's active strike count. Honest criticism is
never sanctioned. Defamation of the team is corrected (not deleted) until it
becomes a repeated pattern.

Each category maps to a list of ``Step``s; the step used = min(strike_count-1,
last). A step with action ``none`` records a strike but takes no action (used to
give defamation two free passes before it's treated as a campaign).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import ModerationAction


@dataclass(frozen=True, slots=True)
class Step:
    action: ModerationAction
    mute_minutes: int = 0
    delete: bool = False


_N = ModerationAction.NONE
_W = ModerationAction.WARN
_D = ModerationAction.DELETE
_M = ModerationAction.MUTE
_B = ModerationAction.BAN

# Ladders indexed by (strike_count - 1), clamped to the last step.
POLICY: dict[str, list[Step]] = {
    # Zero tolerance — no ladder, act on first offense.
    "scam":        [Step(_B, delete=True)],
    "phishing":    [Step(_B, delete=True)],
    "hate_slur":   [Step(_M, 1440, delete=True), Step(_B, delete=True)],
    "doxxing":     [Step(_M, 1440, delete=True), Step(_B, delete=True)],
    # Graduated.
    "harassment":  [Step(_D, delete=True), Step(_M, 60, delete=True),
                    Step(_M, 1440, delete=True), Step(_B, delete=True)],
    "sexual_nsfw": [Step(_D, delete=True), Step(_M, 1440, delete=True), Step(_B, delete=True)],
    # Non-official link from a non-admin → remove the message, every time, but
    # never escalate to mute/ban on the link alone (a good-faith member sharing
    # a news article shouldn't be punished — just kept to official links). Repeat
    # SCAM links are still caught by the scam ladder above (ban).
    "external_link": [Step(_D, delete=True)],
    # Assume good faith: a first ad/spam post gets a friendly WARN (message stays),
    # then escalates to delete/mute/ban only if it keeps happening. Don't crack
    # down on a newcomer's one clumsy link — save enforcement for real offenders.
    "advertising": [Step(_W), Step(_D, delete=True), Step(_M, 60, delete=True), Step(_B, delete=True)],
    "spam":        [Step(_W), Step(_D, delete=True), Step(_M, 60, delete=True), Step(_B, delete=True)],
    "flood":       [Step(_M, 10), Step(_M, 60), Step(_M, 1440)],
    # FUD: honest criticism is NOT this. Coordinated bad-faith fear-mongering is.
    "fud":         [Step(_W), Step(_M, 60, delete=True), Step(_B, delete=True)],
    # Defamation of the team/company: correct-and-drop twice (no delete), then
    # treat as a campaign. The correction itself is handled by the answer path
    # (CORE brand protection) when the bot is addressed.
    "defamation_team": [Step(_N), Step(_N), Step(_M, 60, delete=True), Step(_B, delete=True)],
}

# Categories that always ping admins immediately, regardless of action.
ALERT_CATEGORIES = {"scam", "phishing", "hate_slur", "doxxing", "impersonation"}

# Never sanctioned — these must fall through to the normal pipeline.
SAFE_CATEGORIES = {"criticism", "question", "none"}

# Human-readable reason shown to the user in the DM explanation.
REASONS = {
    "scam": "posting a scam, phishing, or fake-support message",
    "phishing": "a phishing / wallet-draining attempt",
    "hate_slur": "hate speech or a slur",
    "doxxing": "sharing someone's private personal information",
    "harassment": "targeted harassment or a personal insult",
    "sexual_nsfw": "sexual or NSFW content",
    "external_link": "a non-official link (this group allows only official stobox.io links)",
    "advertising": "unsolicited advertising / promotion",
    "spam": "spam",
    "flood": "flooding the chat",
    "fud": "coordinated bad-faith fear-mongering",
    "defamation_team": "repeating false claims about the Stobox team",
    "impersonation": "impersonating Stobox or its team",
}


def decide(category: str, strike_count: int) -> Step:
    steps = POLICY.get(category)
    if not steps:
        return Step(_N)
    idx = min(max(strike_count, 1) - 1, len(steps) - 1)
    return steps[idx]


def reason_text(category: str) -> str:
    return REASONS.get(category, "a community-rules violation")
