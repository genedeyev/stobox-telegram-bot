"""Proactive Intelligence: turn the decision log into insight.

  * :class:`DailyDigest`  — top questions, documentation gaps, potential leads,
    moderation actions, sentiment proxy, languages (spec: "Daily Digest").
  * :class:`WeeklyFAQ`    — cluster recurring questions and generate grounded,
    cited FAQ entries (spec: "Weekly FAQ / Most asked questions").
  * gap detection         — frequent questions the bot answered with low
    confidence = "Missing documentation".

All clustering/aggregation is deterministic and offline-testable; only the FAQ
answer text needs the reasoner.
"""

from .analyzer import (
    QuestionCluster,
    cluster_questions,
    documentation_gaps,
    potential_leads,
    sentiment_proxy,
)
from .digest import DailyDigest
from .faq import WeeklyFAQ

__all__ = [
    "cluster_questions",
    "documentation_gaps",
    "potential_leads",
    "sentiment_proxy",
    "QuestionCluster",
    "DailyDigest",
    "WeeklyFAQ",
]
