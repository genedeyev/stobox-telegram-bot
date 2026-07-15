"""Per-user retention controls: question cap + opt-out (offline)."""

from __future__ import annotations

from stobox_ai.memory.models import UserProfile


def test_record_question_respects_cap():
    p = UserProfile(user_key="t:1")
    for i in range(20):
        p.record_question(f"question {i}?", cap=8)
    assert len(p.recent_questions) == 8
    assert p.recent_questions[-1] == "question 19?"      # keeps the most recent


def test_record_question_default_cap():
    p = UserProfile(user_key="t:1")
    for i in range(20):
        p.record_question(f"q{i}")
    assert len(p.recent_questions) == 15                 # default unchanged
