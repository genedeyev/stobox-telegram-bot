"""Content flywheel — community question-gaps → drafted blog outlines as issues.

The community tells us what it wants to read. This turns the recurring questions
Stoby sees (especially the ones it struggles to answer — real documentation gaps)
into ready-to-write blog outlines, filed as GitHub issues on the content repo so
a human writer can pick them up.

Outlines are DETERMINISTIC (no LLM, no invented facts): a title, the angle, the
exact reader questions to answer, and suggested sections — a brief, not an
article. Dedup is state-based (filed theme keys persist to JSON) so the same
theme is never filed twice, even across restarts. Filing is best-effort and
opt-in; without a GITHUB_TOKEN it degrades to a preview the admins can copy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..insights.analyzer import QuestionCluster, cluster_questions, documentation_gaps
from ..logging import get_logger

log = get_logger(__name__)

CONTENT_LABEL = "content-idea"
_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "are", "with", "that", "this", "you", "your",
         "what", "how", "why", "when", "does", "can", "stobox", "about"}


def theme_key(cluster: QuestionCluster) -> str:
    """Stable dedup key from the most salient words of the exemplar question."""
    toks = sorted(t for t in _TOKEN.findall(cluster.representative.lower())
                  if t not in _STOP)
    return "-".join(toks[:6]) or cluster.representative.lower().strip()[:40]


def draft_outline(cluster: QuestionCluster) -> tuple[str, str]:
    """A blog-outline brief (title, GitHub-issue body) for a question cluster."""
    q = cluster.representative.strip().rstrip("?")
    title = f"Blog outline: {q[:90]}"
    gap_note = ("⚠️ Stoby struggles to answer this confidently — likely a real "
                "documentation gap worth closing.\n\n" if cluster.is_gap else "")
    questions = "\n".join(f"- {m.strip().rstrip('?')}?" for m in cluster.members[:8])
    topics = ", ".join(cluster.topics) or "—"
    body = (
        f"{gap_note}"
        f"**Why now:** the community asked variations of this **{cluster.count}×** "
        f"(avg answer confidence {cluster.avg_confidence:.0%}"
        f"{', ' + str(cluster.unresolved) + ' unresolved' if cluster.unresolved else ''}).\n\n"
        f"**Angle:** a clear, compliance-safe explainer that answers exactly what "
        f"people are asking — grounded in official Stobox docs, no promises.\n\n"
        f"**Reader questions to answer:**\n{questions}\n\n"
        f"**Suggested sections:**\n"
        f"1. The short answer (one paragraph a beginner gets)\n"
        f"2. How it actually works (mechanics, with an example)\n"
        f"3. What to watch for (compliance / jurisdiction caveats — defer to counsel)\n"
        f"4. Next step (Readiness Score / relevant Stobox product)\n\n"
        f"**Topics:** {topics}\n"
        f"**Source:** auto-drafted by Stoby from live community questions. "
        f"Facts must be verified against official docs before publishing."
    )
    return title, body


async def create_issue(repo: str, token: str, title: str, body: str,
                       labels: list[str]) -> int | None:
    """Create a GitHub issue; returns its number, or None on failure."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                json={"title": title, "body": body, "labels": labels},
            )
        if r.status_code >= 300:
            log.error("flywheel.issue_failed", status=r.status_code, body=r.text[:200])
            return None
        return int(r.json().get("number", 0)) or None
    except Exception as exc:  # noqa: BLE001
        log.error("flywheel.issue_error", error=str(exc))
        return None


class ContentFlywheel:
    def __init__(self, repo: str, token: str | None,
                 state_path: str | Path = "data/content_flywheel.json") -> None:
        self.repo = repo
        self.token = token or None
        self.path = Path(state_path)
        self.filed: set[str] = set()
        self._load()

    def pick_themes(self, decisions: list, *, limit: int = 5,
                    min_count: int = 3) -> list[QuestionCluster]:
        """Doc gaps first, then other high-frequency themes; skip already-filed."""
        gaps = documentation_gaps(decisions)
        frequent = [c for c in cluster_questions(decisions) if c.count >= min_count]
        picked: list[QuestionCluster] = []
        seen: set[str] = set()
        for c in gaps + frequent:                       # gaps prioritized
            key = theme_key(c)
            if key in seen or key in self.filed:
                continue
            seen.add(key)
            picked.append(c)
            if len(picked) >= limit:
                break
        return picked

    async def run(self, decisions: list, *, dry_run: bool = True,
                  limit: int = 5) -> list[dict]:
        """Draft outlines for fresh themes; file as issues unless dry_run.

        Returns one dict per theme: {title, body, key, filed, issue}.
        """
        results: list[dict] = []
        for c in self.pick_themes(decisions, limit=limit):
            key = theme_key(c)
            title, body = draft_outline(c)
            entry = {"title": title, "body": body, "key": key,
                     "count": c.count, "is_gap": c.is_gap, "filed": False, "issue": None}
            can_file = not dry_run and self.repo and self.token
            if can_file:
                num = await create_issue(self.repo, self.token, title, body, [CONTENT_LABEL])
                if num:
                    entry["filed"] = True
                    entry["issue"] = num
                    self.filed.add(key)
                    self._save()
            results.append(entry)
        return results

    # -- persistence --------------------------------------------------- #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.filed = set(json.loads(self.path.read_text()).get("filed", []))
        except Exception as exc:  # noqa: BLE001
            log.error("flywheel.load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"filed": sorted(self.filed)}, indent=1))
        except Exception as exc:  # noqa: BLE001
            log.error("flywheel.save_failed", error=str(exc))
