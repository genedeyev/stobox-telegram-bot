"""Mirror captured questions into the stobox-v15 Community QA register.

The register (docs/COMMUNITY-QA.md, private repo) stays the permanent source of
truth: new questions land there as DRAFT sections; approved answers flip them to
APPROVED. Content transforms are pure functions (unit-tested); the push goes
through the GitHub contents API using GITHUB_TOKEN, falling back to the local
`gh` CLI. Mirroring is best-effort — a GitHub outage never blocks the answer
flow (local state + local knowledge file carry it).
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from datetime import UTC, datetime

from ..logging import get_logger
from .register import QAEntry

log = get_logger(__name__)

REPO = "genedeyev/stobox-v15"
PATH = "docs/COMMUNITY-QA.md"
_SECTION_NUM = re.compile(r"^## (\d+)\.", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Pure content transforms
# --------------------------------------------------------------------------- #
def next_section_number(content: str) -> int:
    nums = [int(n) for n in _SECTION_NUM.findall(content)]
    return (max(nums) + 1) if nums else 1


def draft_section(entry: QAEntry, number: int) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return (
        f"\n---\n\n## {number}. {entry.question}\n\n"
        f"**Status:** DRAFT · **Added:** {today} · "
        f"**Source:** telegram bot (auto-captured, asked {entry.ask_count}×)\n\n"
        f"**Answer:**\n\n_(pending — Gene to provide)_\n"
    )


def append_draft(content: str, entry: QAEntry) -> tuple[str, int]:
    n = next_section_number(content)
    return content.rstrip() + "\n" + draft_section(entry, n), n


def approve_section(content: str, number: int, entry: QAEntry) -> str:
    """Flip section `number` to APPROVED with the answer; if the section is
    missing (draft push failed earlier), append it as APPROVED directly."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    pattern = re.compile(
        rf"(## {number}\. .*?)(?=\n---|\n## \d+\.|\Z)", re.DOTALL
    )
    m = pattern.search(content)
    approved = (
        f"## {number}. {entry.question}\n\n"
        f"**Status:** APPROVED · **Added:** {today} · **Source:** telegram bot\n\n"
        f"**Answer:**\n\n{entry.answer}\n"
    )
    if m:
        return content[: m.start()] + approved + content[m.end():]
    return content.rstrip() + "\n\n---\n\n" + approved


# --------------------------------------------------------------------------- #
# GitHub push (best-effort)
# --------------------------------------------------------------------------- #
def _fetch_via_api(token: str) -> tuple[str, str]:
    import httpx

    r = httpx.get(
        f"https://api.github.com/repos/{REPO}/contents/{PATH}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]

def _put_via_api(token: str, new_content: str, sha: str, message: str) -> None:
    import httpx

    r = httpx.put(
        f"https://api.github.com/repos/{REPO}/contents/{PATH}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        json={"message": message, "sha": sha,
              "content": base64.b64encode(new_content.encode()).decode()},
        timeout=20,
    )
    r.raise_for_status()

def _fetch_via_gh() -> tuple[str, str]:
    meta = json.loads(subprocess.run(
        ["gh", "api", f"repos/{REPO}/contents/{PATH}"],
        capture_output=True, text=True, check=True,
    ).stdout)
    return base64.b64decode(meta["content"]).decode("utf-8"), meta["sha"]

def _put_via_gh(new_content: str, sha: str, message: str) -> None:
    encoded = base64.b64encode(new_content.encode()).decode()
    subprocess.run(
        ["gh", "api", "-X", "PUT", f"repos/{REPO}/contents/{PATH}",
         "-f", f"message={message}", "-f", f"content={encoded}", "-f", f"sha={sha}"],
        capture_output=True, text=True, check=True,
    )


def _transform_and_push(transform, message: str) -> int | None:
    """Fetch → transform(content) -> (new_content, number|None) → push."""
    token = os.environ.get("GITHUB_TOKEN")
    try:
        content, sha = _fetch_via_api(token) if token else _fetch_via_gh()
        new_content, number = transform(content)
        if token:
            _put_via_api(token, new_content, sha, message)
        else:
            _put_via_gh(new_content, sha, message)
        return number
    except Exception as exc:  # noqa: BLE001 - mirroring is best-effort
        log.warning("qa.mirror_failed", error=str(exc))
        return None


def push_draft(entry: QAEntry) -> int | None:
    """Append the DRAFT section; returns the register section number."""
    return _transform_and_push(
        lambda c: append_draft(c, entry),
        f"bot: capture community question #{entry.qid} (DRAFT)",
    )


def push_approved(entry: QAEntry) -> int | None:
    def transform(content: str):
        n = entry.register_number or next_section_number(content)
        return approve_section(content, n, entry), n
    return _transform_and_push(
        transform, f"bot: approve community answer #{entry.qid}"
    )
