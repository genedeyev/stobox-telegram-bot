"""Pull the Community Q&A register from the (private) stobox-v15 repo into the
bot's knowledge, keeping ONLY APPROVED entries.

    python scripts/pull_community_qa.py

Uses the `gh` CLI (already authenticated on this machine) since the repo is
private. DRAFT and RETIRED entries are dropped — they must never be served to
the public. The docs watcher hot-reloads the file, so a running bot picks the
update up without a restart. Re-run whenever the register changes.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = "genedeyev/stobox-v15"
PATH = "docs/COMMUNITY-QA.md"
OUT = Path(__file__).resolve().parent.parent / "docs" / "community-qa.md"

FRONT_MATTER = """---
title: Stobox Community Q&A — Canonical Answers
version: "{today}"
author: Stobox
date: {today}
category: community-qa
product: Stobox
language: en
visibility: public
confidence: 1.0
---

# Stobox Community Q&A — Canonical Answers

Approved answers to real questions from the Stobox community. These are the
official, Gene-approved wordings — use their facts exactly.

"""


def fetch() -> str:
    raw = subprocess.run(
        ["gh", "api", f"repos/{REPO}/contents/{PATH}", "--jq", ".content"],
        capture_output=True, text=True, check=True,
    ).stdout
    return base64.b64decode(raw).decode("utf-8")


def approved_sections(md: str) -> list[str]:
    """Split on '## N. Question' headings; keep only APPROVED sections."""
    sections = re.split(r"\n(?=## \d+\.)", md)
    keep = []
    for s in sections:
        if not s.lstrip().startswith("## "):
            continue  # preamble / index — internal process notes, not knowledge
        m = re.search(r"\*\*Status:\*\*\s*(\w+)", s)
        status = (m.group(1).upper() if m else "DRAFT")
        if status == "APPROVED":
            # Strip the status/added metadata line — not for public context.
            s = re.sub(r"\*\*Status:\*\*.*?\n", "", s)
            keep.append(s.strip())
    return keep


def main() -> None:
    md = fetch()
    sections = approved_sections(md)
    if not sections:
        print("No APPROVED sections found — nothing written.", file=sys.stderr)
        raise SystemExit(1)
    body = FRONT_MATTER.format(today=date.today().isoformat()) + "\n\n".join(sections) + "\n"
    OUT.write_text(body, encoding="utf-8")
    print(json.dumps({"written": str(OUT), "approved_sections": len(sections)}))


if __name__ == "__main__":
    main()
