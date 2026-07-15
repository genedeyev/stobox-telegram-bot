"""Name-based addressing: Stoby reacts to his name, never to 'Stobox'."""

from __future__ import annotations

from stobox_ai.channels.telegram.adapter import _NAME_RE


def test_name_matches_stoby_and_typos():
    for t in ["Hey Stoby", "stoby help please", "yo Stobi", "thanks Stobby",
              "Stobbie?", "hey stobey"]:
        assert _NAME_RE.search(t), t


def test_name_does_not_match_stobox():
    for t in ["Stobox is great", "stobox.io is the site", "the Stobox team rocks",
              "I love Stobox Compass"]:
        assert not _NAME_RE.search(t), t
