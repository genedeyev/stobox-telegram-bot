"""Small shared utilities."""

from __future__ import annotations

import json
import re
from typing import Any

_OBJ = re.compile(r"\{.*\}", re.DOTALL)
_ARR = re.compile(r"\[.*\]", re.DOTALL)


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
