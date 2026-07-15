"""Lead qualifier: scoring + MQL summary/handoff (offline, no SMTP)."""

from __future__ import annotations

import pytest

from stobox_ai.leads.qualifier import LeadQualifier
from stobox_ai.memory.models import UserProfile


def _q(config):
    return LeadQualifier(config)


def test_default_inbox_is_info_stobox(config):
    assert _q(config).mql_inbox == "info@stobox.io"


def test_scoring_and_stage(config):
    q = _q(config)
    p = UserProfile(user_key="telegram:1")
    q.update_score(p, buying_intent=True, has_email=False)
    assert p.lead_score == 20 and p.customer_stage == "evaluating"
    q.update_score(p, buying_intent=False, has_email=True)
    assert p.lead_score == 60 and p.customer_stage == "lead"


def test_summary_has_key_fields(config):
    q = _q(config)
    p = UserProfile(user_key="telegram:1", display_name="Gene", email="g@x.com",
                    lead_score=60, customer_stage="lead")
    p.add_product("real_estate")
    p.notes = "asset=real_estate; jurisdiction=us;"
    p.record_question("can I tokenize my building?")
    s = q.summary(p)
    assert "g@x.com" in s and "60/100" in s and "real_estate" in s
    assert "jurisdiction=us" in s and "tokenize my building" in s
    # Points the team at the self-serve routes, not a CRM.
    assert "app.stobox.io" in s and "stobox.io/contact" in s


@pytest.mark.asyncio
async def test_handoff_qualified_returns_true_without_smtp(config):
    q = _q(config)
    p = UserProfile(user_key="telegram:1", email="g@x.com", lead_score=60)
    assert await q.handoff(p) is True          # captured even if no sink configured


@pytest.mark.asyncio
async def test_handoff_skips_unqualified(config):
    q = _q(config)
    assert await q.handoff(UserProfile(user_key="t:1")) is False           # no email
    p = UserProfile(user_key="t:2", email="g@x.com", lead_score=20)
    assert await q.handoff(p) is False                                     # score too low
