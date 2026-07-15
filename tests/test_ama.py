"""AMA collector tests (offline)."""

from __future__ import annotations

from stobox_ai.engagement import AMABook


def test_submit_dedupe_and_votes(tmp_path):
    ama = AMABook(tmp_path / "ama.json")
    ama.open_session("Migration")
    q1, new1 = ama.submit("When exactly does the burn deadline hit?", "t:1", "Alice")
    assert new1 and q1.votes == 1
    # Similar question from another user → merges + upvotes.
    q2, new2 = ama.submit("what time is the burn deadline?", "t:2", "Bob")
    assert not new2 and q2.qid == q1.qid and q2.votes == 2
    # Distinct question → new entry.
    q3, new3 = ama.submit("Will there be a mobile app?", "t:3", "Carol")
    assert new3 and q3.qid != q1.qid


def test_upvote_toggle_and_ranking(tmp_path):
    ama = AMABook(tmp_path / "ama.json")
    ama.open_session()
    a, _ = ama.submit("Question A about tokenizing real estate", "t:1", "A")
    b, _ = ama.submit("Question B about STV3 audits", "t:2", "B")
    # Everyone piles onto B.
    ama.upvote(b.qid, "t:3")
    ama.upvote(b.qid, "t:4")
    ranked = ama.ranked()
    assert ranked[0].qid == b.qid and ranked[0].votes == 3
    # Toggle: same user un-votes.
    assert ama.upvote(b.qid, "t:3") == 2
    # Voting requires no duplicate inflation.
    assert ama.upvote(b.qid, "t:4") == 1


def test_closed_session_and_persistence(tmp_path):
    ama = AMABook(tmp_path / "ama.json")
    ama.open_session("Q3")
    ama.submit("Persisted question?", "t:1", "A")
    ama.close_session()
    ama2 = AMABook(tmp_path / "ama.json")
    assert ama2.open is False and ama2.topic == "Q3"
    assert len(ama2.ranked()) == 1
    ama2.clear()
    assert ama2.ranked() == []
