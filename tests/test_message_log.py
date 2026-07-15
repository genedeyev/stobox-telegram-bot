"""Internal message log tests (offline)."""

from __future__ import annotations

from stobox_ai.ops.message_log import MessageLog


def _log(tmp_path, cap=2000):
    return MessageLog(tmp_path / "mlog.json", cap_per_chat=cap)


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


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "mlog.json"
    ml1 = MessageLog(path)
    ml1.append(chat_id="c1", chat_title="G", user_id="1", username="a",
               display_name="A", text="hi", message_id="1", reply_to="earlier")
    ml2 = MessageLog(path)
    got = ml2.recent("c1", 1)[0]
    assert got.text == "hi" and got.reply_to == "earlier"
