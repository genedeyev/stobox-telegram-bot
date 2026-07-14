"""Unit tests for the golden-gate checker + an offline gate smoke run."""

from __future__ import annotations

import pytest

from evals.run_golden import check_answer, run


def test_check_answer_required_and_forbidden():
    q = {"expect_contains": ["Class-C", "Stobox Tokenized Equities Ltd"],
         "forbid_contains": ["Class-A"]}
    assert check_answer("STBX is Class-C, issued by Stobox Tokenized Equities Ltd.", q).passed
    bad = check_answer("STBX is a Class-A share.", q)
    assert not bad.passed and any("forbidden" in r for r in bad.reasons)
    missing = check_answer("STBX is a token.", q)
    assert not missing.passed and any("missing required" in r for r in missing.reasons)


def test_check_answer_expect_any():
    q = {"expect_any": ["cannot", "can't", "not able"]}
    assert check_answer("I cannot help with that.", q).passed
    assert not check_answer("Sure, here you go.", q).passed


@pytest.mark.asyncio
async def test_golden_gate_passes_offline_deterministic_subset():
    report = await run("evals/golden.yaml")
    # Offline: fact-recall questions are skipped; the deterministic rails must pass.
    assert report["gate"] == "PASS"
    assert report["failed"] == 0
    assert report["passed"] >= 6      # security/injection/advice/impersonation rails
    assert report["skipped"] >= 1     # needs-model questions skipped offline
