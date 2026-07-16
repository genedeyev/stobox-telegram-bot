"""Telegram adapter surface — the audit's least-tested area (was 9% coverage).

Lightweight fakes over python-telegram-bot objects; the real engine runs
offline (EchoLLM + hash embeddings), so update→incoming→reply flows are
exercised end-to-end without the network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from stobox_ai.channels.telegram.adapter import TelegramChannel, strip_html

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeSecrets:
    telegram_token = "123:fake"
    admin_user_ids = {111}
    admin_usernames = {"adminuser"}


class FakeBot:
    def __init__(self, fail_html: bool = False):
        self.sent: list[dict] = []
        self.fail_html = fail_html

    async def send_message(self, chat_id, text, **kwargs):
        from telegram.error import BadRequest

        if self.fail_html and kwargs.get("parse_mode"):
            raise BadRequest("can't parse entities")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

    async def send_chat_action(self, chat_id, action):
        pass

    async def get_chat_member_count(self, chat_id):
        return 1000


class FakeMessage:
    def __init__(self, text="hi", *, fail_html=False):
        self.text = text
        self.caption = None
        self.message_id = 42
        self.message_thread_id = None
        self.reply_to_message = None
        self.forward_origin = None
        self.photo = self.document = self.voice = self.video = self.sticker = None
        self.new_chat_members = []
        self.replies: list[dict] = []
        self._fail_html = fail_html

    async def reply_text(self, text, **kwargs):
        from telegram.error import BadRequest

        if self._fail_html and kwargs.get("parse_mode"):
            raise BadRequest("can't parse entities")
        self.replies.append({"text": text, **kwargs})
        return self

    async def edit_text(self, text, **kwargs):
        # The adapter morphs its "thinking…" placeholder into the answer.
        from telegram.error import BadRequest

        if self._fail_html and kwargs.get("parse_mode"):
            raise BadRequest("can't parse entities")
        self.replies.append({"text": text, "edited": True, **kwargs})
        return self

    async def delete(self):
        self.replies.append({"deleted": True, "text": ""})


def _user(uid=7, username=None, first_name="Ann", is_bot=False):
    return SimpleNamespace(id=uid, username=username, first_name=first_name,
                           full_name=first_name, is_bot=is_bot)


def _chat(cid=-100, ctype="supergroup", title="Community"):
    return SimpleNamespace(id=cid, type=ctype, title=title, username=None)


def _update(message, chat, user, edited=False):
    return SimpleNamespace(
        effective_message=message, effective_chat=chat, effective_user=user,
        edited_message=(message if edited else None), channel_post=None,
    )


@pytest.fixture
async def channel(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    ch = TelegramChannel(engine, secrets=FakeSecrets())
    ch.bot_username = "StobyBot"
    return ch


# --------------------------------------------------------------------------- #
# Admin auth
# --------------------------------------------------------------------------- #

async def test_is_admin_by_id_and_username(channel):
    assert channel.is_admin(_user(uid=111))
    assert channel.is_admin(_user(uid=9, username="AdminUser"))   # case-insensitive
    assert not channel.is_admin(_user(uid=9, username="rando"))
    assert not channel.is_admin(None)


# --------------------------------------------------------------------------- #
# reply_html: splitting + plain-text fallback
# --------------------------------------------------------------------------- #

async def test_reply_html_splits_long_messages_markup_on_last(channel):
    msg = FakeMessage()
    long = "\n\n".join("para " + "x" * 800 for _ in range(10))   # ~8k chars
    await channel.reply_html(msg, long, reply_markup="MARKUP")
    assert len(msg.replies) >= 2
    assert all(len(r["text"]) <= 4096 for r in msg.replies)
    assert msg.replies[-1]["reply_markup"] == "MARKUP"
    assert all(r["reply_markup"] is None for r in msg.replies[:-1])


async def test_reply_html_falls_back_to_plain_on_bad_html(channel):
    msg = FakeMessage(fail_html=True)
    await channel.reply_html(msg, "<b>bold</b> and <bad")
    assert len(msg.replies) == 1
    assert "parse_mode" not in msg.replies[0] or not msg.replies[0].get("parse_mode")
    assert "<b>" not in msg.replies[0]["text"]     # stripped


def test_strip_html_removes_only_telegram_tags():
    assert strip_html("<b>hi</b> a<b") == "hi a<b"


# --------------------------------------------------------------------------- #
# Addressing + incoming translation
# --------------------------------------------------------------------------- #

async def test_is_addressed_by_name_typo_and_reply(channel):
    m = FakeMessage("hey stobby, you there?")
    assert channel._is_addressed(m, m.text)
    m2 = FakeMessage("talking about stobox the company")
    assert not channel._is_addressed(m2, m2.text)
    m3 = FakeMessage("@stobybot what is STBU?")
    assert channel._is_addressed(m3, m3.text)
    m4 = FakeMessage("and you?")
    m4.reply_to_message = SimpleNamespace(
        from_user=SimpleNamespace(username="StobyBot"), text="prev")
    assert channel._is_addressed(m4, m4.text)


async def test_to_incoming_maps_fields(channel):
    msg = FakeMessage("look https://stobox.io now")
    upd = _update(msg, _chat(), _user(uid=5, username="ann"))
    inc = channel._to_incoming(upd)
    assert inc.author.external_id == "5"
    assert inc.links == ["https://stobox.io"]
    assert not inc.is_edited
    assert inc.chat_id == "-100"


# --------------------------------------------------------------------------- #
# Welcome path: HTML-escaped names, ghost skip, raid guard
# --------------------------------------------------------------------------- #

async def test_welcome_escapes_hostile_display_name(channel):
    evil = _user(uid=8, first_name='<a href="https://scam">Stobox Support</a>')
    msg = FakeMessage()
    msg.new_chat_members = [evil]
    ctx = SimpleNamespace(bot=FakeBot())
    await channel._on_new_members(_update(msg, _chat(), evil), ctx)
    assert msg.replies, "welcome must be sent"
    text = msg.replies[0]["text"]
    assert "<a href=" not in text            # neutralized
    assert "&lt;a href=" in text             # rendered as text


async def test_mass_join_suppresses_welcome_and_pings_admins(channel):
    msg = FakeMessage()
    msg.new_chat_members = [_user(uid=100 + i, first_name=f"u{i}") for i in range(7)]
    bot = FakeBot()
    await channel._on_new_members(_update(msg, _chat(), msg.new_chat_members[0]),
                                  SimpleNamespace(bot=bot))
    assert not msg.replies                                   # no welcome
    assert any(s["chat_id"] == 111 for s in bot.sent)        # admin pinged


# --------------------------------------------------------------------------- #
# Persistence: known chats + milestones survive a restart
# --------------------------------------------------------------------------- #

async def test_known_chats_persist_across_restart(channel, config):
    channel.remember_chat("-100555")
    channel._member_milestones["-100555"] = 1000
    channel._save_state()

    from stobox_ai.core.engine import AgentEngine
    engine2 = await AgentEngine.create(config)
    reborn = TelegramChannel(engine2, secrets=FakeSecrets())
    assert "-100555" in reborn.known_chats
    assert reborn._member_milestones["-100555"] == 1000


async def test_member_milestone_fires_once(channel):
    bot = FakeBot()
    ctx = SimpleNamespace(bot=bot)
    chat = _chat(cid=-100777)
    await channel._member_milestone(ctx, chat)     # count=1000 → celebrates
    await channel._member_milestone(ctx, chat)     # same milestone → silent
    celebrations = [s for s in bot.sent if "members" in s["text"]]
    assert len(celebrations) == 1
    # And the dedupe is on disk, not just RAM.
    saved = json.loads(channel._state_path.read_text())
    assert saved["milestones"]["-100777"] == 1000


# --------------------------------------------------------------------------- #
# Follow-up question cache
# --------------------------------------------------------------------------- #

async def test_remember_question_roundtrip_and_bound(channel):
    token = channel._remember_question("what is STBU?")
    assert channel._q_cache[token] == "what is STBU?"
    for i in range(600):                       # exceed the 500 bound
        channel._remember_question(f"q{i}")
    assert len(channel._q_cache) <= 501


# --------------------------------------------------------------------------- #
# End-to-end DM: update → engine → reply (offline echo stack)
# --------------------------------------------------------------------------- #

async def test_dm_question_gets_a_reply(channel):
    msg = FakeMessage("What is the STBU token?")
    upd = _update(msg, _chat(cid=77, ctype="private"), _user(uid=77))
    await channel._on_message(upd, SimpleNamespace(bot=FakeBot()))
    assert msg.replies, "a private question must always get some reply"


async def test_group_chatter_not_addressed_is_ignored(channel):
    msg = FakeMessage("lol nice weather")
    upd = _update(msg, _chat(), _user(uid=12))
    await channel._on_message(upd, SimpleNamespace(bot=FakeBot()))
    assert not msg.replies
    assert "-100" in channel.known_chats       # but the chat was remembered
