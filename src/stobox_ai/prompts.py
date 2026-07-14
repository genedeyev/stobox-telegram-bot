"""Prompt library loader.

Prompts live as versioned YAML under ``config/prompts/`` — never hardcoded in
code (spec: "Store prompts separately. Never hardcode. Version prompts. A/B test
prompts."). Each file:

    id: answer_synthesis
    active: v2
    versions:
      v1: { weight: 0, template: "..." }
      v2: { weight: 1, template: "..." }

``render(id, **vars)`` picks a version (active, or weighted A/B sample keyed by a
stable bucket) and formats it. Rendering is deterministic given a bucket key, so
the same user in the same experiment always sees the same variant.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

import yaml

from .logging import get_logger

log = get_logger(__name__)


class PromptLibrary:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self._cache: dict[str, dict] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.root.exists():
            log.warning("prompts.missing", path=str(self.root))
            return
        for path in self.root.glob("*.y*ml"):
            data = yaml.safe_load(path.read_text()) or {}
            pid = data.get("id", path.stem)
            self._cache[pid] = data
        log.info("prompts.loaded", count=len(self._cache))

    def _pick_version(self, spec: dict, bucket: str | None) -> tuple[str, dict]:
        versions: dict[str, dict] = spec.get("versions", {})
        if not versions:
            return "inline", {"template": spec.get("template", "")}
        weighted = [(k, float(v.get("weight", 0))) for k, v in versions.items()]
        total = sum(w for _, w in weighted)
        if bucket and total > 0:  # deterministic A/B bucketing
            h = int(hashlib.sha256(f"{spec.get('id')}:{bucket}".encode()).hexdigest(), 16)
            point = (h % 10_000) / 10_000 * total
            acc = 0.0
            for name, w in weighted:
                acc += w
                if point <= acc:
                    return name, versions[name]
        active = spec.get("active") or next(iter(versions))
        return active, versions.get(active, next(iter(versions.values())))

    def render(self, prompt_id: str, *, bucket: str | None = None, **variables) -> str:
        spec = self._cache.get(prompt_id)
        if not spec:
            raise KeyError(f"Unknown prompt id: {prompt_id!r}")
        version, chosen = self._pick_version(spec, bucket)
        template = chosen.get("template", "")
        try:
            return template.format(**variables)
        except KeyError as exc:
            log.error("prompts.missing_var", prompt=prompt_id, var=str(exc))
            return template

    def version_of(self, prompt_id: str, bucket: str | None = None) -> str:
        spec = self._cache.get(prompt_id, {})
        return self._pick_version(spec, bucket)[0] if spec else "unknown"


@lru_cache(maxsize=1)
def get_prompts() -> PromptLibrary:
    return PromptLibrary(os.environ.get("PROMPTS_PATH", "config/prompts"))
