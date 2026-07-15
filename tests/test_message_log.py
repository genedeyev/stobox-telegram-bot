"""Internal message log tests (offline)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stobox_ai.ops.message_log import LoggedMessage, MessageLog


def _log(tmp_path, cap=2000):
    return MessageLog(tmp_path / "mlog.jsonl", cap_per_chat=cap)


def _add(ml, chat, uid, name, text, uname=None):
    ml.append(chat_id=chat, chat_title="G", user_id=uid, username=uname,
              display_name=name, text=text, message_id="1")


def test_append_recent_and_total(tmp_path):
    ml = _log(tmp_path)
    _add(ml, "c1", "1", "Arevik", "hello team", uname="arevikd")
    _add(ml, "c1", "2", "Gene", "how does migration work?")
    assert ml.total("c1") == 2
    recent = ml.recent("c1", 5)
    assert [m.display_name for m in recent] == ["Arevik", "Gene"]


def test_search_and_by_user(tmp_path):
    ml = _log(tmp_path)
    _add(ml, "c1", "1", "Arevik", "the sources must stay in sync", uname="arevikd")
    _add(ml, "c1", "2", "Gene", "totally agree")
    assert len(ml.search("c1", "sources")) == 1
    assert len(ml.by_user("c1", "@arevikd")) == 1
    assert len(ml.by_user("c1", "1")) == 1          # by id
    assert len(ml.by_user("c1", "gene")) == 1       # by display-name substring


def test_cap_drops_oldest(tmp_path):
    ml = _log(tmp_path, cap=3)
    for i in range(6):
        _add(ml, "c1", str(i), f"U{i}", f"msg {i}")
    assert ml.total("c1") == 3
    assert [m.text for m in ml.recent("c1", 10)] == ["msg 3", "msg 4", "msg 5"]


def test_relevant_finds_older_related_message(tmp_path):
    ml = _log(tmp_path)
    _add(ml, "c1", "1", "A", "we confirmed the STBU burn address on the portal")
    for i in range(14):
        _add(ml, "c1", str(i + 2), f"U{i}", f"random chatter number {i}")
    hits = ml.relevant("c1", "what was the STBU burn address again?", n=4, exclude_recent=12)
    assert any("burn address" in h.text for h in hits)


def test_relevant_returns_empty_within_working_window(tmp_path):
    ml = _log(tmp_path)
    for i in range(5):                                # fewer than exclude_recent
        _add(ml, "c1", str(i), "U", "migration migration topic")
    assert ml.relevant("c1", "migration", exclude_recent=12) == []


def test_age_retention_drops_old_messages(tmp_path):
    ml = MessageLog(tmp_path / "m.jsonl", retention_days=90)
    old = LoggedMessage(
        at=(datetime.now(UTC) - timedelta(days=120)).isoformat(),
        chat_id="c1", chat_title="G", user_id="1", username=None,
        display_name="Old", text="ancient", message_id="0",
    )
    ml.chats["c1"] = [old]
    _add(ml, "c1", "2", "New", "fresh message")      # append triggers prune
    texts = [m.text for m in ml.recent("c1", 10)]
    assert "ancient" not in texts and "fresh message" in texts


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "mlog.jsonl"
    ml1 = MessageLog(path)
    ml1.append(chat_id="c1", chat_title="G", user_id="1", username="a",
               display_name="A", text="hi", message_id="1", reply_to="earlier")
    ml2 = MessageLog(path)
    got = ml2.recent("c1", 1)[0]
    assert got.text == "hi" and got.reply_to == "earlier"
