"""Golden-question regression gate (ARCHITECTURE.md §5).

Runs evals/golden.yaml against the live engine and enforces every trap: required
substrings must appear, forbidden substrings must not. Used as the promotion gate
before a new index/prompt goes live, and in CI on PRs touching SYSTEM-PROMPT.md /
canonicals.yaml.

Questions tagged `needs_model: true` recall canonical facts and require a real
reasoner; offline (no API key → stub LLM) they are SKIPPED, while the
deterministic-rail questions (refusals, security, injection, anti-impersonation)
still run and must pass. With ANTHROPIC_API_KEY/OPENAI_API_KEY set, everything runs.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import yaml

from stobox_ai.config import load_config
from stobox_ai.core.engine import AgentEngine
from stobox_ai.core.types import Author, ChatType, IncomingMessage
from stobox_ai.logging import configure_logging


@dataclass
class Check:
    passed: bool
    reasons: list[str]


def check_answer(answer: str, q: dict) -> Check:
    """Pure checker — unit-tested independently of the engine."""
    low = (answer or "").lower()
    reasons: list[str] = []
    for s in q.get("expect_contains", []):
        if s.lower() not in low:
            reasons.append(f"missing required: {s!r}")
    any_of = q.get("expect_any", [])
    if any_of and not any(s.lower() in low for s in any_of):
        reasons.append(f"none of expected-any present: {any_of}")
    for s in q.get("forbid_contains", []):
        if s.lower() in low:
            reasons.append(f"contains forbidden: {s!r}")
    return Check(passed=not reasons, reasons=reasons)


def _msg(text: str, i: int) -> IncomingMessage:
    return IncomingMessage(
        author=Author(external_id=f"golden-{i}", display_name="Golden"),
        text=text, chat_id=f"golden-{i}", chat_type=ChatType.PRIVATE,
        message_id=str(i), raw={"addressed": True},
    )


async def run(path: str) -> dict:
    spec = yaml.safe_load(Path(path).read_text())
    questions = spec.get("questions", [])
    min_pass = float(spec.get("meta", {}).get("min_pass_rate", 1.0))

    engine = await AgentEngine.create(load_config())
    offline = engine.reasoner.name == "echo"

    results = []
    for i, q in enumerate(questions):
        if q.get("needs_model") and offline:
            results.append({"id": q["id"], "status": "skipped", "reasons": ["needs model (offline)"]})
            continue
        resp = await engine.handle(_msg(q["question"], i))
        answer = resp.text if resp else ""
        chk = check_answer(answer, q)
        results.append({
            "id": q["id"], "status": "pass" if chk.passed else "fail",
            "reasons": chk.reasons, "answer": answer[:200],
        })

    graded = [r for r in results if r["status"] in ("pass", "fail")]
    passed = [r for r in graded if r["status"] == "pass"]
    pass_rate = (len(passed) / len(graded)) if graded else 1.0
    return {
        "offline": offline,
        "total": len(questions),
        "graded": len(graded),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "passed": len(passed),
        "failed": len(graded) - len(passed),
        "pass_rate": round(pass_rate, 3),
        "min_pass_rate": min_pass,
        "gate": "PASS" if pass_rate >= min_pass else "FAIL",
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the golden-question regression gate.")
    parser.add_argument("--path", default="evals/golden.yaml")
    parser.add_argument("--no-fail", action="store_true", help="report only; don't exit nonzero")
    args = parser.parse_args()

    configure_logging("WARNING")
    report = asyncio.run(run(args.path))
    print(f"\nGolden gate: {report['gate']}  "
          f"(passed {report['passed']}/{report['graded']}, skipped {report['skipped']}, "
          f"offline={report['offline']})")
    for r in report["results"]:
        mark = {"pass": "✓", "fail": "✗", "skipped": "–"}[r["status"]]
        print(f"  {mark} {r['id']}" + (f"  → {'; '.join(r['reasons'])}" if r["reasons"] and r["status"] == "fail" else ""))
    if report["gate"] == "FAIL" and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
