"""Growth Wave 1 tests: attribution, reminders, shareable flag, drafts."""

from __future__ import annotations

import pytest

from stobox_ai.core.types import Author, ChatType, IncomingMessage
from stobox_ai.ops.reminders import THRESHOLDS, ReminderBook


# --------------------------------------------------------------------------- #
# Reminder book
# --------------------------------------------------------------------------- #
def test_reminder_book_state(tmp_path):
    book = ReminderBook(tmp_path / "r.json")
    assert book.subscribe("111") is True
    assert book.subscribe("111") is False          # idempotent
    assert book.subscribe("222") is True
    assert book.is_subscribed("111")
    assert book.unsubscribe("111") is True
    assert book.unsubscribe("111") is False
    book.mark_sent("burn-7")
    assert book.was_sent("burn-7") and not book.was_sent("burn-1")
    # Persistence roundtrip.
    book2 = ReminderBook(tmp_path / "r.json")
    assert book2.is_subscribed("222") and book2.was_sent("burn-7")
    assert 0 in THRESHOLDS and 30 in THRESHOLDS


# --------------------------------------------------------------------------- #
# Shareable flag
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_shareable_only_on_cited_confident_answers(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)

    def m(t, uid="s1"):
        return IncomingMessage(author=Author(external_id=uid), text=t, chat_id=uid,
            chat_type=ChatType.PRIVATE, message_id="1", raw={"addressed": True})

    good = await engine.handle(m("What is the STBU token?"))
    assert good.meta.get("shareable") is True          # cited + confident

    refusal = await engine.handle(m("should I buy STBU?", uid="s2"))
    assert not refusal.meta.get("shareable")           # intercepts never shareable

    engine.confidence.threshold = 1.01                 # force the IDK gate
    gated = await engine.handle(m("what is the staking APY?", uid="s3"))
    assert not gated.meta.get("shareable")             # gated answers never shareable


# --------------------------------------------------------------------------- #
# Attribution (start payload logic mirrors commands.start_cmd)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_attribution_first_touch_and_referral(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    p = await engine.memory.get_profile("telegram:42", "Newbie")
    assert p.source == ""
    p.source = "blog_stv3"                             # first touch
    await engine.memory.save_profile(p)
    # Second /start with a different payload must NOT overwrite first touch.
    p2 = await engine.memory.get_profile("telegram:42")
    if not p2.source:
        p2.source = "x_post"
    assert p2.source == "blog_stv3"

    ref = await engine.memory.get_profile("telegram:7", "Referrer")
    ref.referrals += 1
    await engine.memory.save_profile(ref)
    assert (await engine.memory.get_profile("telegram:7")).referrals == 1


# --------------------------------------------------------------------------- #
# Draft answers
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_draft_answer_offline_returns_text_or_empty(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    draft = await engine.draft_answer("What is the STBU token?")
    # Offline echo LLM returns a stub string (>=20 chars, no NO_DRAFT) — the
    # contract is: str, never raises.
    assert isinstance(draft, str)


def test_qa_entry_draft_persists(tmp_path):
    from stobox_ai.qa.register import QARegister

    reg = QARegister(tmp_path / "qa.json")
    e, _ = reg.capture("Mystery question?", channel="telegram", chat_id="c",
                       message_id="1", user_key="t:1")
    e.draft = "Proposed wording."
    reg._save()
    reg2 = QARegister(tmp_path / "qa.json")
    assert reg2.get(e.qid).draft == "Proposed wording."
