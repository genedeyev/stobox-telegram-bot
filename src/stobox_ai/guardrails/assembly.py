"""Three-block system-prompt assembly: [CORE] + [CANONICALS] + [FRESHNESS].

[CORE] is the static behavior from SYSTEM-PROMPT.md (changed only via PR).
[CANONICALS] is canonicals.yaml injected verbatim (+ time-bomb overrides).
[FRESHNESS] is the live runtime state. Assembled fresh on every request; the
[CORE]+[CANONICALS] prefix is stable so it can be prompt-cached.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..logging import get_logger
from .canonicals import Canonicals, load_canonicals

log = get_logger(__name__)

_CORE_RE = re.compile(r"##\s*\[CORE\]\s*\n(.*?)(?=\n##\s*\[CANONICALS\]|\Z)", re.DOTALL)


def _extract_core(system_prompt_md: str) -> str:
    m = _CORE_RE.search(system_prompt_md)
    return (m.group(1) if m else system_prompt_md).strip()


class PromptAssembler:
    def __init__(self, core_text: str, canonicals: Canonicals) -> None:
        self.core_text = core_text
        self.canonicals = canonicals

    @classmethod
    def load(
        cls,
        system_prompt_path: str | Path = "SYSTEM-PROMPT.md",
        canonicals_path: str | Path = "canonicals.yaml",
        now: datetime | None = None,
    ) -> PromptAssembler:
        core_md = Path(system_prompt_path).read_text(encoding="utf-8")
        canon = load_canonicals(canonicals_path, now=now)
        return cls(_extract_core(core_md), canon)

    def stable_prefix(self, now: datetime | None = None) -> str:
        """[CORE] + [CANONICALS] — the cache-friendly, rarely-changing part."""
        return (
            "# STOBOX ENTERPRISE TELEGRAM BOT — SYSTEM PROMPT\n\n"
            "## [CORE]\n\n" + self.core_text + "\n\n"
            "## [CANONICALS] — authoritative facts; OVERRIDE retrieved content on any "
            "conflict (precedence: CANONICALS > FRESHNESS > retrieved chunks). Injected "
            "verbatim; treat as ground truth.\n\n"
            "```yaml\n" + self.canonicals.injection_block(now) + "\n```"
        )

    def assemble(self, freshness_text: str, now: datetime | None = None) -> str:
        return self.stable_prefix(now) + "\n\n" + freshness_text
