"""XP / streaks / levels / leaderboard tests (offline)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stobox_ai.engagement import XPBook, level_for


def test_levels():
    assert level_for(0)[1] == "Newcomer"
    assert level_for(200)[1] == "Regular"
    assert level_for(400)[1] == "Tokenization Scholar"
    assert level_for(5000)[1] == "Community OG"


def test_award_and_rank(tmp_path):
    book = XPBook(tmp_path / "xp.json")
    book.award("t:1", 100, "q", "Alice")
    book.award("t:2", 60, "q", "Bob")
    book.award("t:1", 20, "quiz", "Alice")
    assert book.get("t:1").xp == 120
    assert book.rank("t:1") == 1 and book.rank("t:2") == 2
    top = book.top(10)
    assert [u.user_key for u in top] == ["t:1", "t:2"]
    # Persistence roundtrip.
    book2 = XPBook(tmp_path / "xp.json")
    assert book2.get("t:1").xp == 120


def test_streak_increments_and_resets(tmp_path):
    book = XPBook(tmp_path / "xp.json")
    streak, new = book.touch("t:1", "Alice")
    assert streak == 1 and new
    # Same day → no change.
    assert book.touch("t:1")[0] == 1
    # Simulate: last active yesterday → streak grows.
    rec = book.get("t:1")
    rec.last_active = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert book.touch("t:1")[0] == 2
    # Gap of 3 days → reset to 1.
    rec.last_active = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d")
    assert book.touch("t:1")[0] == 1
    assert book.get("t:1").best_streak == 2


def test_weekly_leaderboard(tmp_path):
    book = XPBook(tmp_path / "xp.json")
    book.award("t:1", 40, "q")
    weekly = book.top(10, weekly=True)
    assert weekly and weekly[0].xp_week == 40


@pytest.mark.asyncio
async def test_engine_awards_xp_on_helpful_answer(config):
    from stobox_ai.core.engine import AgentEngine
    from stobox_ai.core.types import Author, ChatType, IncomingMessage

    engine = await AgentEngine.create(config)

    def msg():
        return IncomingMessage(author=Author(external_id="99", display_name="Gamer"),
            text="What is the STBU token?", chat_id="c", chat_type=ChatType.PRIVATE,
            message_id="1", raw={"addressed": True})

    r = await engine.handle(msg())
    if r.confidence.value != "low":     # cited, confident → XP awarded
        rec = engine.xp.get("telegram:99")
        assert rec and rec.xp > 0 and rec.streak >= 1
