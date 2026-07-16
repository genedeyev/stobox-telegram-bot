"""Small shared utilities."""

from __future__ import annotations

import json
import re
from typing import Any

_OBJ = re.compile(r"\{.*\}", re.DOTALL)
_ARR = re.compile(r"\[.*\]", re.DOTALL)


def filter_dataclass_kwargs(cls: type, d: dict) -> dict:
    """Drop keys a dataclass no longer knows.

    Persisted JSON/JSONB payloads outlive schema changes: hydrating an old row
    with ``Cls(**d)`` after a field was removed/renamed raises TypeError on
    every load — bricking that user/record forever. Filtering to the current
    field set makes removals safe; additions are already safe via defaults.
    """
    from dataclasses import fields

    known = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in known}


def extract_json(text: str, want: str = "object") -> Any | None:
    """Best-effort parse of a JSON object/array embedded in model output.

    Models sometimes wrap JSON in prose or code fences. Returns the parsed value
    or ``None`` — never raises — so callers can fall back cleanly.
    """
    if not text:
        return None
    pattern = _ARR if want == "array" else _OBJ
    match = pattern.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
