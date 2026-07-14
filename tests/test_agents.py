"""Confidence engine, moderation heuristics, lead extraction (offline)."""

from __future__ import annotations

from stobox_ai.agents.confidence import ConfidenceEngine
from stobox_ai.knowledge.models import Chunk, DocMeta, RetrievedChunk
from stobox_ai.leads.qualifier import LeadQualifier


def test_confidence_parse_strips_trailer():
    eng = ConfidenceEngine(threshold=0.55)
    answer = "STBU is a utility token.\nCONFIDENCE: 0.9\nSOURCES: STBU Token Overview"
    clean, self_conf, sources = eng.parse(answer)
    assert "CONFIDENCE" not in clean and "SOURCES" not in clean
    assert self_conf == 0.9
    assert sources == ["STBU Token Overview"]


def test_confidence_gates_without_citations():
    eng = ConfidenceEngine(threshold=0.55, require_citations=True)
    rc = RetrievedChunk(chunk=Chunk(doc_id="d", text="x", meta=DocMeta("t", "f")), score=0.9)
    with_cite = eng.score([rc], self_conf=0.9, cited=True)
    without_cite = eng.score([rc], self_conf=0.9, cited=False)
    assert without_cite < with_cite
    assert eng.below_threshold(eng.score([], self_conf=None, cited=False))


def test_lead_email_extraction_and_scoring():
    q = LeadQualifier.__new__(LeadQualifier)
    q.enabled = True
    q.webhook = None
    assert LeadQualifier.extract_email("reach me at jane@example.com please") == "jane@example.com"
    assert LeadQualifier.extract_email("no email here") is None

    from stobox_ai.memory.models import UserProfile

    p = UserProfile(user_key="telegram:1")
    q.update_score(p, buying_intent=True, has_email=True)
    assert p.lead_score >= 60
    assert p.customer_stage == "lead"
