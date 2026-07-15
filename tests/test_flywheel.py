"""Content flywheel tests (offline: outline drafting + dedup, no GitHub)."""

from __future__ import annotations

import pytest

from stobox_ai.content.flywheel import ContentFlywheel, draft_outline, theme_key
from stobox_ai.insights.analyzer import QuestionCluster


def _cluster(rep, count=3, unresolved=0, topics=None):
    return QuestionCluster(
        representative=rep, count=count, members=[rep, rep + " really?"],
        topics=topics or ["tokenization"], avg_confidence=0.4, unresolved=unresolved,
    )


def test_theme_key_is_stable_and_topic_bearing():
    c1 = _cluster("How do I tokenize real estate in the US?")
    assert theme_key(c1) == theme_key(c1)                 # stable
    assert "tokenize" in theme_key(c1) and "estate" in theme_key(c1)


def test_draft_outline_shape():
    c = _cluster("Can I tokenize a fund?", count=5, unresolved=3)
    title, body = draft_outline(c)
    assert title.startswith("Blog outline:")
    assert "5×" in body and "Reader questions to answer" in body
    assert "Suggested sections" in body
    assert "verified against official docs" in body


def test_gap_note_only_when_gap():
    gap = _cluster("weird edge question", count=4, unresolved=3)   # is_gap True
    ok = _cluster("popular clear question", count=6, unresolved=0)  # not a gap
    assert gap.is_gap and "documentation gap" in draft_outline(gap)[1]
    assert not ok.is_gap and "documentation gap" not in draft_outline(ok)[1]


def test_pick_themes_skips_already_filed(tmp_path):
    fw = ContentFlywheel("owner/repo", token=None, state_path=tmp_path / "fw.json")

    class D:  # minimal Decision-like
        def __init__(self, q, conf="low"):
            self.question = q
            self.mode = "community_manager"
            self.confidence = conf
            self.confidence_score = 0.3
            self.meta = {"topics": ["rwa"]}

    decisions = [D("How to tokenize real estate?") for _ in range(4)]
    picked = fw.pick_themes(decisions, limit=5, min_count=2)
    assert picked, "should surface a recurring theme"
    fw.filed.add(theme_key(picked[0]))                    # mark filed
    picked2 = fw.pick_themes(decisions, limit=5, min_count=2)
    assert all(theme_key(c) != theme_key(picked[0]) for c in picked2)


@pytest.mark.asyncio
async def test_run_dry_run_does_not_file(tmp_path):
    fw = ContentFlywheel("owner/repo", token="ghtoken", state_path=tmp_path / "fw.json")

    class D:
        def __init__(self, q):
            self.question, self.mode = q, "community_manager"
            self.confidence, self.confidence_score = "low", 0.3
            self.meta = {"topics": ["rwa"]}

    decisions = [D("How to tokenize a building?") for _ in range(4)]
    results = await fw.run(decisions, dry_run=True, limit=3)
    assert results and all(r["filed"] is False for r in results)
    assert not fw.filed                                   # nothing recorded as filed
