"""Rate limiter + kill switch tests (offline)."""

from __future__ import annotations

import pytest

from stobox_ai.core.types import Author, ChatType, IncomingMessage
from stobox_ai.ops import RateLimiter
from stobox_ai.ops.ratelimit import RateStatus


def test_rate_limiter_per_minute():
    rl = RateLimiter(per_minute=3, per_day=100, global_daily_output_tokens=None)
    assert all(rl.check("u1").allowed for _ in range(3))
    d = rl.check("u1")
    assert not d.allowed and d.status == RateStatus.PER_MINUTE
    # A different user is unaffected.
    assert rl.check("u2").allowed


def test_rate_limiter_per_day():
    rl = RateLimiter(per_minute=1000, per_day=5, global_daily_output_tokens=None)
    for _ in range(5):
        assert rl.check("u1").allowed
    assert rl.check("u1").status == RateStatus.PER_DAY


def test_webhook_signature_verification():
    from stobox_ai.ops.webhook import sign, verify_signature

    body = b'{"sha":"abc","source":"vercel-deploy"}'
    good = sign("s3cret", body)
    assert verify_signature("s3cret", body, good)
    assert not verify_signature("s3cret", body, "sha256=deadbeef")       # wrong sig
    assert not verify_signature("wrong", body, good)                      # wrong secret
    assert not verify_signature(None, body, good)                         # secret unset
    assert not verify_signature("s3cret", body, None)                     # header missing


def test_rate_limiter_global_cap():
    rl = RateLimiter(per_minute=1000, per_day=1000, global_daily_output_tokens=100)
    assert rl.check("u1").allowed
    rl.record_spend(150)
    assert rl.over_global_cap
    assert rl.check("u2").status == RateStatus.GLOBAL_CAP


def _msg(text: str, uid: str = "1", admin: bool = False) -> IncomingMessage:
    return IncomingMessage(
        author=Author(external_id=uid, is_admin=admin), text=text, chat_id="c",
        chat_type=ChatType.PRIVATE, message_id="1", raw={"addressed": True},
    )


@pytest.mark.asyncio
async def test_kill_switch_serves_static_faq(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    engine.pause("incident")
    resp = await engine.handle(_msg("What is Stobox Compass?"))
    assert resp.meta.get("paused")
    assert "stobox.io" in resp.text.lower()
    engine.resume()
    resp2 = await engine.handle(_msg("What is Stobox Compass?"))
    assert not resp2.meta.get("paused")


@pytest.mark.asyncio
async def test_engine_rate_limits_non_admin(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    engine.rate_limiter.per_minute = 2
    for _ in range(2):
        await engine.handle(_msg("what is compass?", uid="rl"))
    limited = await engine.handle(_msg("what is compass?", uid="rl"))
    assert limited.meta.get("rate_limited")
    # Admins bypass the limiter.
    admin = await engine.handle(_msg("what is compass?", uid="admin", admin=True))
    assert not admin.meta.get("rate_limited")
