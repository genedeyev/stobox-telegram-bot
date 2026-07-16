"""AXIS pre-qualifier tests (offline)."""

from __future__ import annotations

from stobox_ai.leads import axis


def _answer(session, choices):
    """choices: list of option indices, one per question."""
    for i, idx in enumerate(choices):
        session.record(axis.QUESTIONS[i], idx)


def test_session_flow_and_scoring():
    s = axis.Session()
    assert not s.done and s.step == 0
    # Strong: ready-to-raise (3) + $10-50M (3) + this quarter (3) = 9.
    _answer(s, [0, 0, 2, 2, 0])
    assert s.done and s.score == 9
    assert axis.band(s.score) == "strong"
    assert s.answers["stage"] == "ready" and s.answers["asset"] == "real_estate"


def test_bands():
    assert axis.band(9) == "strong"
    assert axis.band(7) == "strong"
    assert axis.band(5) == "promising"
    assert axis.band(4) == "promising"
    assert axis.band(2) == "early"
    assert axis.band(0) == "early"


def test_result_text_has_cta_and_disclaimer():
    s = axis.Session()
    _answer(s, [1, 1, 2, 3, 0])       # fund, EU, ready → Raisable (not Compass)
    text = axis.result_text(s, "Gene")
    assert axis.RAISABLE_URL in text          # stage "ready" routes to the Raise layer
    assert "not investment advice" in text.lower()
    assert "Gene" in text
    # Exploring → assess first with the Readiness Score.
    early = axis.Session()
    _answer(early, [0, 0, 0, 0, 3])   # exploring everywhere → early
    assert axis.band(early.score) == "early"
    assert axis.READINESS_URL in axis.result_text(early)


def test_next_step_routes_by_stage():
    assert axis.next_step("raising")[1] == axis.APP_URL
    assert axis.next_step("ready")[1] == axis.RAISABLE_URL
    assert axis.next_step("exploring")[1] == axis.READINESS_URL
    assert axis.next_step("have_asset")[1] == axis.READINESS_URL


def test_every_question_has_options():
    assert len(axis.QUESTIONS) == 5
    for q in axis.QUESTIONS:
        assert 2 <= len(q.options) <= 4
        assert all(len(o) == 3 for o in q.options)   # (label, value, points)
