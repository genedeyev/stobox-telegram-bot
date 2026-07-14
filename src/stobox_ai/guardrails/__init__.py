"""Compliance guardrail layer for the Stobox enterprise bot.

Implements the spec's three-block prompt (`[CORE] + [CANONICALS] + [FRESHNESS]`),
the canonicals precedence + time-bombing rules, and the deterministic behavioral
rails that must hold regardless of what the model generates.
"""

from .assembly import PromptAssembler
from .canonicals import Canonicals, load_canonicals
from .freshness import FreshnessBuilder, MigrationPhase
from .rails import ComplianceRails, RailResult

__all__ = [
    "Canonicals",
    "load_canonicals",
    "FreshnessBuilder",
    "MigrationPhase",
    "PromptAssembler",
    "ComplianceRails",
    "RailResult",
]
