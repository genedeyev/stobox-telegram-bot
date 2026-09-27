"""Stoby acceptance: stage 0 attempts, run against production.

Each criterion is an ATTEMPT with one unambiguous outcome, never a read of a
setting. Four outcomes, the last two matter as much as the first two:

  выполнено      done, proven in this run
  НЕ ВЫПОЛНЕНО   a defect, with the evidence
  не готово      the work or the time window is not there yet
  человек        a person has to check this; the machine can't

Usage (prod log attempts need a directory linked to the Railway service, so no
project ids live in this public repo):

  STOBY_RAILWAY_DIR=/path/linked/to/service python scripts/acceptance.py \
      --commit <sha deployed from main> [--skip-model]

--skip-model skips A0-3 (the golden gate calls the real model and costs money).
Exit code is 1 if any attempt is НЕ ВЫПОЛНЕНО.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

DONE, FAIL, NOT_READY, HUMAN = "выполнено", "НЕ ВЫПОЛНЕНО", "не готово", "человек"

# Events that mean Stoby spoke on its own timer. Stage 0: none of them, ever.
PRESENCE_EVENTS = [
    "proactive.evangelist_posted", "proactive.quiz_posted", "updates.briefing_posted",
    "migration.countdown_posted", "migration.preopen_posted", "migration.window_opened",
    "migration.claims_announced", "revival.prompt_shared", "revival.blog_shared",
    "reminders.blast", "winback.nudged",
]
QUIET_HOURS_REQUIRED = 24
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class Result:
    code: str
    outcome: str
    evidence: str


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _deployment(rdir: str, commit: str) -> tuple[Result, datetime | None]:
    p = _run(["railway", "deployment", "list", "--json"], cwd=rdir, timeout=60)
    if p.returncode != 0:
        return Result("A0-0", FAIL, f"railway deployment list failed: {p.stderr.strip()[:200]}"), None
    deps = json.loads(p.stdout)
    live = [d for d in deps if d.get("status") == "SUCCESS"]
    if not live:
        return Result("A0-0", FAIL, "no SUCCESS deployment"), None
    top = live[0]
    sha = (top.get("meta") or {}).get("commitHash", "")
    started = datetime.fromisoformat(top["createdAt"].replace("Z", "+00:00"))
    if not sha.startswith(commit):
        return (Result("A0-0", NOT_READY,
                       f"live deployment is {sha[:9]}, expected {commit[:9]}"), None)
    return Result("A0-0", DONE, f"deployment {top['id'][:8]} SUCCESS on {sha[:9]}, "
                                f"since {started:%d.%m.%Y %H:%M} UTC"), started


class Logs:
    """Server-side filtered reads. Railway caps --lines (20000 is refused), so
    one unfiltered read of a day silently comes back short or empty; each
    needle is fetched with its own filter instead. `readable()` proves the
    reads work at all: a clean result from an unreadable log is not clean."""

    MAX = 5000

    def __init__(self, rdir: str, since: datetime) -> None:
        self.rdir, self.since = rdir, since.strftime("%Y-%m-%dT%H:%M:%SZ")

    def count(self, needle: str) -> int:
        p = _run(["railway", "logs", "--since", self.since, "-n", str(self.MAX),
                  "-f", needle], cwd=self.rdir, timeout=300)
        if p.returncode != 0 or "Error" in p.stdout[:200]:
            raise RuntimeError(f"railway logs failed for {needle!r}: {(p.stderr or p.stdout)[:200]}")
        return sum(1 for ln in p.stdout.splitlines() if needle in ANSI.sub("", ln))

    def readable(self) -> bool:
        return self.count("_heartbeat_job") > 0


def attempt_quiet(logs: Logs, started: datetime) -> Result:
    if logs.count("proactive.scheduled") == 0:
        return Result("A0-1", NOT_READY, "no proactive.scheduled line since the deploy: "
                                         "cannot prove the new build booted")
    hits = {e: logs.count(e) for e in PRESENCE_EVENTS}
    spoke = {e: n for e, n in hits.items() if n}
    if spoke:
        return Result("A0-1", FAIL, f"Stoby posted on a timer: {spoke}")
    hours = (datetime.now(UTC) - started).total_seconds() / 3600
    if hours < QUIET_HOURS_REQUIRED:
        return Result("A0-1", NOT_READY, f"0 presence posts in {hours:.1f} h; "
                                         f"the window is {QUIET_HOURS_REQUIRED} h")
    return Result("A0-1", DONE, f"0 presence posts in {hours:.1f} h after the deploy")


def attempt_state(logs: Logs) -> Result:
    denied = logs.count("Permission denied")
    if denied:
        return Result("A0-2", FAIL, f"{denied} 'Permission denied' lines since the deploy")
    wrote = logs.count("xp.award") + logs.count("decision ")
    if not wrote:
        return Result("A0-2", NOT_READY, "0 denials, but no message has been processed yet, "
                                         "so no state write has been attempted")
    return Result("A0-2", DONE, f"0 denials; {wrote} state-writing events since the deploy")


def attempt_leads(logs: Logs) -> Result:
    if logs.count("lead.handoff_rejected") or logs.count("lead.handoff_failed"):
        return Result("A0-6", FAIL, "a lead was sent and the CRM intake refused or failed")
    if logs.count('"lead.handoff"') or logs.count("lead.handoff "):
        return Result("A0-6", DONE, "a lead reached the CRM intake since the deploy")
    if logs.count("lead.captured_no_sink"):
        return Result("A0-6", NOT_READY, "leads are captured but no CRM webhook is set "
                                         "(CRM_WEBHOOK_URL / CRM_WEBHOOK_SECRET)")
    return Result("A0-6", NOT_READY, "no lead since the deploy")


def attempt_tests() -> Result:
    p = _run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
              "tests/test_stage0.py"])
    tail = (p.stdout.strip().splitlines() or ["(no output)"])[-1]
    return Result("A0-4/5", DONE if p.returncode == 0 else FAIL, tail)


def attempt_golden() -> Result:
    p = _run([sys.executable, "-m", "evals.run_golden"], timeout=1800)
    line = next((ln for ln in p.stdout.splitlines() if "Golden gate" in ln), p.stdout[-200:])
    if "offline=True" in line:
        return Result("A0-3", NOT_READY, "no model key: model traps were skipped")
    return Result("A0-3", DONE if p.returncode == 0 else FAIL, line.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", required=True, help="sha expected live on Railway")
    ap.add_argument("--skip-model", action="store_true")
    args = ap.parse_args()

    results: list[Result] = []
    rdir = os.environ.get("STOBY_RAILWAY_DIR")
    if not rdir:
        results.append(Result("A0-0", NOT_READY, "STOBY_RAILWAY_DIR not set"))
        started = None
    else:
        dep, started = _deployment(rdir, args.commit)
        results.append(dep)
    logs = Logs(rdir, started) if rdir and started else None
    if logs and not logs.readable():
        results.append(Result("A0-L", FAIL, "production logs are not readable: no heartbeat "
                                            "line since the deploy, every log attempt is void"))
        logs = None
    if logs:
        results += [attempt_quiet(logs, started), attempt_state(logs)]
    else:
        results += [Result("A0-1", NOT_READY, "no live deployment to read"),
                    Result("A0-2", NOT_READY, "no live deployment to read")]
    results.append(attempt_golden() if not args.skip_model
                   else Result("A0-3", NOT_READY, "skipped (--skip-model)"))
    results.append(attempt_tests())
    results.append(attempt_leads(logs) if logs
                   else Result("A0-6", NOT_READY, "no live deployment to read"))
    results.append(Result("A0-7", HUMAN, "a live question in the group gets a correct answer"))

    width = max(len(r.outcome) for r in results)
    for r in results:
        print(f"{r.code:<7} {r.outcome:<{width}}  {r.evidence}")
    done = sum(r.outcome == DONE for r in results)
    fails = sum(r.outcome == FAIL for r in results)
    print(f"\n{done} of {len(results)} done, {fails} defects.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
