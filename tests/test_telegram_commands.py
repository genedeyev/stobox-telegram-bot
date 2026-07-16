"""Telegram command surface — cooldowns, admin fan-out, escaping, /forgetme."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_telegram_adapter import FakeBot, FakeMessage, FakeSecrets, _chat, _update, _user

from stobox_ai.channels.telegram import commands as cmd
from stobox_ai.channels.telegram.adapter import TelegramChannel


@pytest.fixture
async def ctx(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    adapter = TelegramChannel(engine, secrets=FakeSecrets())
    adapter.bot_username = "StobyBot"
    bot = FakeBot()
    context = SimpleNamespace(
        bot=bot, args=[], bot_data={"engine": engine, "adapter": adapter},
    )
    return context


def _upd(text="", uid=7, username=None, ctype="private", first_name="Ann"):
    msg = FakeMessage(text)
    return _update(msg, _chat(cid=uid if ctype == "private" else -100, ctype=ctype),
                   _user(uid=uid, username=username, first_name=first_name)), msg


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    cmd._CMD_LAST.clear()
    yield
    cmd._CMD_LAST.clear()


# --------------------------------------------------------------------------- #
# /support and /report: admin fan-out + per-user cooldown
# --------------------------------------------------------------------------- #

async def test_support_dms_admins_once_then_cools_down(ctx):
    upd, msg = _upd("/support", uid=5)
    await cmd.support_cmd(upd, ctx)
    assert any(s["chat_id"] == 111 for s in ctx.bot.sent)     # admin DM'd
    n = len(ctx.bot.sent)
    await cmd.support_cmd(upd, ctx)                            # immediate repeat
    assert len(ctx.bot.sent) == n                              # NO second fan-out
    assert "already been flagged" in msg.replies[-1]["text"]


async def test_report_cooldown_is_per_user(ctx):
    upd_a, _ = _upd("x", uid=5)
    upd_b, _ = _upd("x", uid=6)
    ctx.args = ["spam", "in", "chat"]
    await cmd.report_cmd(upd_a, ctx)
    before = len(ctx.bot.sent)
    await cmd.report_cmd(upd_b, ctx)                           # different user
    assert len(ctx.bot.sent) > before                          # still fans out


# --------------------------------------------------------------------------- #
# /appeal: reason reaches admins HTML-escaped
# --------------------------------------------------------------------------- #

async def test_appeal_escapes_reason(ctx):
    upd, msg = _upd(uid=9, first_name="Bob")
    ctx.args = ["<b>unfair</b>", "&", "wrong"]
    await cmd.appeal_cmd(upd, ctx)
    admin_dm = next(s for s in ctx.bot.sent if s["chat_id"] == 111)
    assert "<b>unfair</b>" not in admin_dm["text"]
    assert "&lt;b&gt;unfair&lt;/b&gt;" in admin_dm["text"]
    assert msg.replies       # user got the confirmation


# --------------------------------------------------------------------------- #
# /forgetme: DM-only, actually erases
# --------------------------------------------------------------------------- #

async def test_forgetme_requires_dm(ctx):
    upd, msg = _upd("/forgetme", uid=13, ctype="supergroup")
    await cmd.forgetme_cmd(upd, ctx)
    assert "direct message" in msg.replies[0]["text"]


async def test_forgetme_erases_user_state(ctx):
    engine = ctx.bot_data["engine"]
    engine.xp.award("telegram:13", 10, "test", "Ann")
    engine.reminders.subscribe("13")
    upd, msg = _upd("/forgetme", uid=13)
    await cmd.forgetme_cmd(upd, ctx)
    assert engine.xp.get("telegram:13") is None
    assert not engine.reminders.is_subscribed("13")
    assert "deleted" in msg.replies[0]["text"]


# --------------------------------------------------------------------------- #
# Admin gating + /log escaping
# --------------------------------------------------------------------------- #

async def test_admin_commands_ignore_non_admins(ctx):
    upd, msg = _upd("/log", uid=999)          # not an admin
    await cmd.log_cmd(upd, ctx)
    assert not msg.replies and not ctx.bot.sent


async def test_log_escapes_member_messages(ctx):
    engine = ctx.bot_data["engine"]
    engine.message_log.append(
        chat_id="111", chat_title="t", user_id="5", username="x",
        display_name="<i>Evil</i>", text="hello <script>alert(1)</script>",
        message_id="1",
    )
    upd, msg = _upd("/log", uid=111)          # admin, private chat id 111
    await cmd.log_cmd(upd, ctx)
    text = msg.replies[0]["text"]
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;i&gt;Evil&lt;/i&gt;" in text


# --------------------------------------------------------------------------- #
# /check cooldown protects the chain RPCs
# --------------------------------------------------------------------------- #

async def test_check_cooldown(ctx, monkeypatch):
    calls = 0

    async def fake_check(addr):
        nonlocal calls
        calls += 1
        return "report"

    monkeypatch.setattr(ctx.bot_data["engine"], "check_wallet", fake_check)
    upd, msg = _upd(uid=5)
    ctx.args = ["0x" + "a" * 40]
    await cmd.check_cmd(upd, ctx)
    await cmd.check_cmd(upd, ctx)              # immediate repeat
    assert calls == 1
    assert "try again" in msg.replies[-1]["text"].lower()
