"""Resource-matcher tests (offline). Verifies grounding + no fabrication."""

from __future__ import annotations

from stobox_ai.leads import matcher


def _link_count(text: str) -> int:
    return text.count("http://") + text.count("https://")


def test_match_is_short_single_link_and_disclaimer():
    text = matcher.match("real_estate", "us", "Gene")
    assert matcher.READINESS_URL in text
    assert "Gene" in text and "real estate" in text and "the US" in text
    assert "not legal or investment advice" in text.lower()
    # Link discipline: at most 2, and no bulleted menu.
    assert _link_count(text) <= 2
    assert "•" not in text and "1." not in text


def test_match_unknown_asset_and_jurisdiction_fall_back():
    text = matcher.match("spaceship", "mars")
    assert "your asset" in text and "your region" in text
    assert matcher.READINESS_URL in text          # still routes to the real next step


def test_match_defers_to_counsel_for_us():
    # Jurisdiction framing must defer to counsel, never assert a legal conclusion.
    text = matcher.match("equity", "us")
    assert "counsel" in text.lower()


def test_match_never_fabricates_case_studies():
    # No client names / outcome claims — only general education + real links.
    text = matcher.match("fund", "eu").lower()
    for banned in ("case study", "our client", "raised $", "returns of", "guaranteed"):
        assert banned not in text


def test_resources_overview_is_grounded():
    text = matcher.resources_overview()
    assert matcher.READINESS_URL in text
    assert "/qualify" in text
    assert "not legal or investment advice" in text.lower()
