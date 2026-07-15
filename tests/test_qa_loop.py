"""Unanswered-question loop tests (offline)."""

from __future__ import annotations

import pytest

from stobox_ai.qa.mirror import append_draft, approve_section, next_section_number
from stobox_ai.qa.register import QAEntry, QARegister

_REGISTER = """# Community Q&A — Canonical Answers

## Index

## 1. Old question?

**Status:** APPROVED · **Added:** 2026-07-14

**Answer:**

Old answer.

---

## 2. Another question?

**Status:** APPROVED · **Added:** 2026-07-14

**Answer:**

Another answer.
"""


# --------------------------------------------------------------------------- #
# Register state machine
# --------------------------------------------------------------------------- #
def test_capture_dedupe_and_answer(tmp_path):
    reg = QARegister(tmp_path / "qa.json")
    e1, new1 = reg.capture("What is the staking APY for STBU?",
                           channel="telegram", chat_id="c1", message_id="1", user_key="t:1")
    assert new1 and e1.qid == 1
    # Similar question from another user → same entry, second asker.
    e2, new2 = reg.capture("what's the STBU staking APY?",
                           channel="telegram", chat_id="c2", message_id="9", user_key="t:2")
    assert not new2 and e2.qid == 1 and len(e2.askers) == 2 and e2.ask_count == 2
    # Different question → new entry.
    e3, new3 = reg.capture("When does the Qatar office open?",
                           channel="telegram", chat_id="c3", message_id="2", user_key="t:3")
    assert new3 and e3.qid == 2
    assert len(reg.pending()) == 2

    done = reg.answer(1, "There is no staking program for STBU.")
    assert done.status == "answered" and len(reg.pending()) == 1

    # Persistence roundtrip.
    reg2 = QARegister(tmp_path / "qa.json")
    assert reg2.get(1).status == "answered"
    assert reg2.get(2).status == "pending"
    assert len(reg2.get(1).askers) == 2


# --------------------------------------------------------------------------- #
# Register-file content transforms
# --------------------------------------------------------------------------- #
def test_append_draft_numbers_continue():
    entry = QAEntry(qid=7, question="Is there a Stobox mobile app?")
    new_content, n = append_draft(_REGISTER, entry)
    assert n == 3
    assert "## 3. Is there a Stobox mobile app?" in new_content
    assert "**Status:** DRAFT" in new_content
    assert next_section_number(new_content) == 4


def test_approve_section_flips_draft():
    entry = QAEntry(qid=7, question="Is there a Stobox mobile app?")
    drafted, n = append_draft(_REGISTER, entry)
    entry.answer = "Not today — the platform is web-first at app.stobox.io."
    approved = approve_section(drafted, n, entry)
    assert "**Status:** APPROVED" in approved.split(f"## {n}.")[1]
    assert "web-first at app.stobox.io" in approved
    assert "_(pending" not in approved.split(f"## {n}.")[1]
    # Old sections untouched.
    assert "Old answer." in approved and "Another answer." in approved


def test_approve_section_appends_when_draft_missing():
    entry = QAEntry(qid=9, question="Ghost question?", answer="Ghost answer.")
    out = approve_section(_REGISTER, 5, entry)
    assert "## 5. Ghost question?" in out and "Ghost answer." in out


# --------------------------------------------------------------------------- #
# Engine integration: IDK gate captures the question
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_idk_gate_captures_question(config, tmp_path, monkeypatch):
    from stobox_ai.core.engine import AgentEngine
    from stobox_ai.core.types import Author, ChatType, IncomingMessage

    engine = await AgentEngine.create(config)
    engine.qa = QARegister(tmp_path / "qa.json")     # isolated state
    engine.confidence.threshold = 1.01               # force the gate for any answer

    msg = IncomingMessage(author=Author(external_id="u1"), text="What is the Stobox staking APY?",
        chat_id="chat9", chat_type=ChatType.PRIVATE, message_id="42", raw={"addressed": True})
    r = await engine.handle(msg)
    assert r.meta.get("gated")
    qa = r.meta.get("qa")
    assert qa and qa["new"] and qa["qid"] == 1
    entry = engine.qa.get(1)
    assert entry.askers[0]["chat_id"] == "chat9"
    assert entry.askers[0]["message_id"] == "42"
    # Friendly flagged-to-team reply, not the old robotic IDK.
    assert "flag" in r.text.lower() or "team" in r.text.lower()
