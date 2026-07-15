"""Resource-matcher tests (offline). Verifies grounding + no fabrication."""

from __future__ import annotations

from stobox_ai.leads import matcher


def test_match_uses_real_urls_and_disclaimer():
    text = matcher.match("real_estate", "us", "Gene")
    assert matcher.READINESS_URL in text
    assert matcher.LEARN_STV3_URL in text
    assert matcher.APP_URL in text and matcher.CONTACT_URL in text
    assert "Gene" in text and "real estate" in text and "the US" in text
    assert "not legal or investment advice" in text.lower()


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
