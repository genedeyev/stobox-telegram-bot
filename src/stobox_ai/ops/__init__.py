"""Operational safety: rate limiting, spend cap, kill switch (ARCHITECTURE.md §7)."""

from .ratelimit import RateDecision, RateLimiter

__all__ = ["RateLimiter", "RateDecision"]
