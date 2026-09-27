"""Grounding: every figure and address in a draft must be in the sources.

A model can restate a fact wrong even when the right fact is in front of it.
Before a draft is sent, each 0x address and each number in it is looked up in
the source text; anything not found rejects the draft. Small counting words
("1:1", "one", single digits) are allowed.
"""

from __future__ import annotations

import re

_ADDR = re.compile(r"0x[0-9a-fA-F]{40,64}")
_NUM = re.compile(r"\$?\d[\d,.:]*\d%?|\$?\d%?")
_TRIVIAL = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "1:1", "24"}


def _norm(s: str) -> str:
    return s.replace(",", "").replace("$", "").rstrip(".%").lower()


def ungrounded(draft: str, sources: str) -> list[str]:
    src_low = sources.lower()
    src_norm = _norm(sources)
    missing: list[str] = []
    for a in _ADDR.findall(draft):
        if a.lower() not in src_low:
            missing.append(a)
    for n in _NUM.findall(_ADDR.sub(" ", draft)):
        n = n.rstrip(".,:")
        if n in _TRIVIAL or len(_norm(n)) <= 1:
            continue
        if n.lower() in src_low or _norm(n) in src_norm:
            continue
        missing.append(n)
    return missing
