"""Topic-subscription book tests (offline, tmp-file state)."""

from __future__ import annotations

from stobox_ai.ops.subscriptions import (
    TOPICS,
    SubscriptionBook,
    classify_topic,
    valid_topic,
)


def _book(tmp_path):
    return SubscriptionBook(tmp_path / "subs.json")


def test_subscribe_toggle_and_query(tmp_path):
    b = _book(tmp_path)
    assert b.topics_for("42") == []
    assert b.subscribe("42", "migration") is True
    assert b.subscribe("42", "migration") is False        # idempotent
    assert b.subscribe("42", "bogus") is False            # unknown topic rejected
    assert b.topics_for("42") == ["migration"]
    assert b.subscribers_for("migration") == [("42", "en")]
    assert b.subscribers_for("product") == []


def test_unsubscribe_drops_empty_record(tmp_path):
    b = _book(tmp_path)
    b.subscribe("7", "product")
    assert b.unsubscribe("7", "product") is True
    assert b.unsubscribe("7", "product") is False
    assert "7" not in b.subs                              # empty record removed


def test_toggle(tmp_path):
    b = _book(tmp_path)
    assert b.toggle("9", "rwa-news") is True              # now on
    assert b.toggle("9", "rwa-news") is False             # now off
    assert b.topics_for("9") == []


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "subs.json"
    b1 = SubscriptionBook(path)
    b1.subscribe("5", "migration")
    b1.subscribe("5", "product")
    b2 = SubscriptionBook(path)                           # reload from disk
    assert sorted(b2.topics_for("5")) == ["migration", "product"]


def test_unsubscribe_all(tmp_path):
    b = _book(tmp_path)
    b.subscribe("3", "migration")
    b.subscribe("3", "product")
    assert b.unsubscribe_all("3") is True
    assert b.unsubscribe_all("3") is False
    assert b.topics_for("3") == []


def test_valid_topic():
    assert valid_topic("migration") and valid_topic("rwa-news") and valid_topic("product")
    assert not valid_topic("nonsense")
    assert set(TOPICS) == {"migration", "rwa-news", "product"}


def test_classify_topic_routing():
    assert classify_topic("STBU migration deadline is near", "") == "migration"
    assert classify_topic("New Compass feature release", "") == "product"
    assert classify_topic("How regulators view real-world assets", "tokenization") == "rwa-news"
    assert classify_topic("A cooking recipe", "unrelated content") is None
    # Specific topics win over the broad rwa-news bucket.
    assert classify_topic("Tokenization: migrate your STBU", "") == "migration"
