"""Analytics dashboard HTML renderer (offline)."""

from __future__ import annotations

from stobox_ai.insights.dashboard import render_dashboard


def test_empty_digest_renders_placeholder():
    html = render_dashboard({"count": 0, "empty": True})
    assert "<!doctype html>" in html.lower()
    assert "No activity recorded" in html


def test_populated_digest_renders_sections_and_escapes():
    digest = {
        "count": 42,
        "sentiment": {"label": "watch", "health_score": 0.62,
                      "unanswered_rate": 0.1, "moderation_rate": 0.05},
        "top_questions": [{"question": "What is <Compass>?", "asked": 9, "topics": ["compass"]}],
        "documentation_gaps": [{"question": "edge case q", "asked": 4,
                                "unresolved": 3, "avg_confidence": 0.3}],
        "potential_leads": [{"user_key": "telegram:1", "touches": 3,
                             "captured": True, "last_q": "pricing?"}],
        "moderation_actions": [{"category": "spam", "action": "delete"}],
        "escalations": 1,
        "languages": [["en", 30], ["ru", 12]],
        "metrics": {"count": 42, "avg_latency_ms": 800},
    }
    html = render_dashboard(digest)
    assert "Stoby — Community Analytics" in html
    assert "watch" in html and "Documentation gaps" in html
    assert "telegram:1" in html
    # User-derived content is HTML-escaped (no raw angle brackets injected).
    assert "&lt;Compass&gt;" in html and "<Compass>" not in html
