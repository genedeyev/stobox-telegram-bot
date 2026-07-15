"""Router sentiment heuristics (offline, no LLM)."""

from __future__ import annotations

from stobox_ai.agents.router import HOT_SENTIMENTS, IntentRouter, Routing


def test_sentiment_default_is_neutral():
    assert Routing().sentiment == "neutral"


def test_heuristic_detects_fud():
    for text in ("this is a total scam", "looks like a rugpull tbh", "dead project, exit scam"):
        assert IntentRouter._heuristic(text).sentiment == "fud"


def test_heuristic_detects_anger():
    assert IntentRouter._heuristic("this is bullshit and you're all liars").sentiment == "angry"
    assert IntentRouter._heuristic("worst experience ever!!!").sentiment == "angry"


def test_heuristic_detects_frustration():
    assert IntentRouter._heuristic("it's still not working and I'm stuck").sentiment == "frustrated"


def test_heuristic_neutral_smalltalk():
    for text in ("gm everyone", "hello there", "nice"):
        assert IntentRouter._heuristic(text).sentiment == "neutral"


def test_fud_takes_precedence_over_anger():
    # A message with both scam-language and anger markers routes to fud.
    assert IntentRouter._heuristic("this scam is bullshit").sentiment == "fud"


def test_hot_sentiments_membership():
    assert "fud" in HOT_SENTIMENTS and "angry" in HOT_SENTIMENTS
    assert "neutral" not in HOT_SENTIMENTS and "positive" not in HOT_SENTIMENTS
