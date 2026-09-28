"""Stoby stage 1 acceptance: attempts A1-1…A1-7, each with one unambiguous outcome.

  python scripts/acceptance_stage1.py [--evals FILE.json] [--live-sig-down]

Outcomes: выполнено · НЕ ВЫПОЛНЕНО · не готово · человек. Never a read of a
setting: every green line is a thing that happened in this run, or a recorded
eval run given with --evals (written by `python -m evals.run_stoby --out`).
Exit 1 on any НЕ ВЫПОЛНЕНО.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DONE, FAIL, NOT_READY, HUMAN = "выполнено", "НЕ ВЫПОЛНЕНО", "не готово", "человек"
ADDR = re.compile(r"0x[0-9a-fA-F]{40}")
NEEDLES = ("31 December 2026", "216,563,456", "$305M", "250,000,000", "15 September 2026")
CEILING_MEAN, CEILING_P95, MIN_ANSWERS = 0.03, 0.06, 30


@dataclass
class R:
    code: str
    outcome: str
    evidence: str


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


def a1_1() -> R:
    files = [f for f in _git("ls-files").splitlines()
             if f.startswith(("src/stoby/", "config/prompts/stoby"))]
    bad = []
    for f in files:
        text = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
        if ADDR.search(text) or any(n in text for n in NEEDLES):
            bad.append(f)
    if bad:
        return R("A1-1", FAIL, f"facts inside the stage 1 package: {bad}")
    legacy = [f for f in _git("ls-files").splitlines()
              if f in ("canonicals.yaml", "SYSTEM-PROMPT.md") or f.startswith(("docs/", "src/stobox_ai/"))]
    if legacy:
        return R("A1-1", NOT_READY, f"package clean; {len(legacy)} stage 0 fact/code files remain until the cutover")
    return R("A1-1", DONE, "no fact files, addresses or figures anywhere in the tree")


def _pytest(expr: str) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
                        "tests/stoby", "-k", expr], cwd=ROOT, capture_output=True, text=True)
    return p.returncode == 0, (p.stdout.strip().splitlines() or ["(no output)"])[-1]


def a1_2() -> R:
    ok, tail = _pytest("a1_2")
    return R("A1-2", DONE if ok else FAIL, f"offline site swap: {tail}; live preview run is stage step 11")


def _evals(path: str | None, set_name: str, code: str, min_n: int = 1) -> R:
    if not path or not Path(path).exists():
        return R(code, NOT_READY, "no --evals file (python -m evals.run_stoby --out FILE)")
    rows = [r for r in json.loads(Path(path).read_text()) if r["set"] == set_name]
    if len(rows) < min_n:
        return R(code, NOT_READY, f"{len(rows)} {set_name} rows in the file, need {min_n}")
    failed = [r["id"] for r in rows if not r["pass"]]
    if failed:
        return R(code, FAIL, f"{len(failed)}/{len(rows)} failed: {failed[:6]}")
    return R(code, DONE, f"{len(rows)}/{len(rows)} passed")


async def _live_sig_down() -> R:
    import os

    from stoby.answer import CANT_VERIFY, Pipeline
    from stoby.config import Settings
    from stoby.ledger import SpendLedger
    from stoby.llm import Answerer
    from stoby.sources.sig import SigClient
    from stoby.sources.site import SiteSource

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return R("A1-5", NOT_READY, "live half needs ANTHROPIC_API_KEY (the model must be reachable and unused)")
    s = Settings.from_env()
    calls = {"n": 0}

    class Counting(Answerer):
        async def draft(self, *a, **k):
            calls["n"] += 1
            return await super().draft(*a, **k)

    site = SiteSource(s.site_url, s.state_dir, s.site_cache_minutes, s.site_stale_hours)
    sig = SigClient("http://127.0.0.1:9/mcp", timeout_s=1.0)
    p = Pipeline(site, sig, Counting(s.answer_model), SpendLedger(s.state_dir, s.daily_cap_usd))
    reply = await p.answer("What is Stobox Intelligence?", chat="acceptance", user="a1-5")
    if reply.text == CANT_VERIFY and calls["n"] == 0 and reply.outcome == "fixed_sig_down":
        return R("A1-5", DONE, "live: SIG at 127.0.0.1:9 gave the fixed text, 0 model calls")
    return R("A1-5", FAIL, f"outcome {reply.outcome}, model calls {calls['n']}")


def a1_5(live: bool) -> R:
    ok, tail = _pytest("a1_5")
    if not ok:
        return R("A1-5", FAIL, f"offline: {tail}")
    if not live:
        return R("A1-5", DONE, f"offline: {tail}; add --live-sig-down for the live half")
    return asyncio.run(_live_sig_down())


def a1_7(path: str | None) -> R:
    if not path or not Path(path).exists():
        return R("A1-7", NOT_READY, "no --evals file")
    usd = sorted(r["usd"] for r in json.loads(Path(path).read_text()) if r["outcome"] == "answered")
    if len(usd) < MIN_ANSWERS:
        return R("A1-7", NOT_READY, f"{len(usd)} answered rows, need {MIN_ANSWERS}")
    mean, p95 = sum(usd) / len(usd), usd[int(0.95 * (len(usd) - 1))]
    ok = mean <= CEILING_MEAN and p95 <= CEILING_P95
    return R("A1-7", DONE if ok else FAIL,
             f"{len(usd)} answers: mean ${mean:.4f} (≤ {CEILING_MEAN}), p95 ${p95:.4f} (≤ {CEILING_P95})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evals", help="json written by evals.run_stoby --out")
    ap.add_argument("--live-sig-down", action="store_true")
    a = ap.parse_args()
    rows = [a1_1(), a1_2(), _evals(a.evals, "golden", "A1-3", 30), _evals(a.evals, "injection", "A1-4", 20),
            a1_5(a.live_sig_down),
            R("A1-6", HUMAN, "a person sends the scenarios in the test group; the machine grades the answer lines"),
            a1_7(a.evals)]
    w = max(len(r.outcome) for r in rows)
    for r in rows:
        print(f"{r.code:<6} {r.outcome:<{w}}  {r.evidence}")
    fails = sum(r.outcome == FAIL for r in rows)
    print(f"\n{sum(r.outcome == DONE for r in rows)} of {len(rows)} done, {fails} defects.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
