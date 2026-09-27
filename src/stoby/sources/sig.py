"""SIG, the Stobox Intelligence Graph, over its MCP JSON-RPC endpoint.

One batched POST per question: `stobox_answer_context` for the question and, in a
second call, `stobox_fact_check` for the draft. A batch counts as one call
against SIG's quota. Any failure raises SigDown, and Stoby then does not answer
from the model at all (Gene, 27.09.2026: "SIG down means no model answer").
Stoby never calls `stobox_start_tokenization`, the one write tool.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx

READ_TOOLS = frozenset({"stobox_answer_context", "stobox_fact_check", "stobox_term_lookup"})
_SCRUB = [
    (re.compile(r"0x[0-9a-fA-F]{40,64}"), "[address]"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?<!\w)@\w{3,}"), "[handle]"),
    (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "[number]"),
]


class SigDown(Exception):
    """SIG could not be read: timeout, 5xx, quota, auth or a malformed reply."""


def scrub(text: str) -> str:
    """Remove addresses, emails, handles and phone numbers before a question
    leaves for SIG's demand log."""
    for pat, repl in _SCRUB:
        text = pat.sub(repl, text)
    return text[:500]


@dataclass
class FactCheck:
    verdict: str
    issues: list[dict]
    correction: str | None


class SigClient:
    def __init__(self, url: str, token: str = "", timeout_s: float = 8.0,
                 client: httpx.AsyncClient | None = None, breaker_s: float = 60.0) -> None:
        self.url, self.token, self.timeout = url, token, timeout_s
        self.client = client
        self.breaker_s = breaker_s
        self._down_until = 0.0

    async def _batch(self, calls: list[tuple[str, dict]]) -> list[dict]:
        if time.time() < self._down_until:
            raise SigDown("circuit open after a recent failure")
        for name, _ in calls:
            if name not in READ_TOOLS:
                raise ValueError(f"Stoby only calls read tools, not {name}")
        body = [{"jsonrpc": "2.0", "id": i, "method": "tools/call",
                 "params": {"name": n, "arguments": a}} for i, (n, a) in enumerate(calls)]
        headers = {"content-type": "application/json",
                   "accept": "application/json, text/event-stream"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            r = await client.post(self.url, json=body, headers=headers, timeout=self.timeout)
            if r.status_code != 200:
                raise SigDown(f"HTTP {r.status_code}")
            text = r.text
            data = json.loads(text[text.find("["):]) if "[" in text[:5] else json.loads(text)
            data = data if isinstance(data, list) else [data]
            out: list[dict] = [{}] * len(calls)
            for item in data:
                if "error" in item:
                    raise SigDown(f"rpc error {item['error'].get('code')}")
                content = item["result"]["content"][0]["text"]
                out[int(item["id"])] = json.loads(content)
            if any(not x for x in out):
                raise SigDown("missing result in batch")
            return out
        except SigDown:
            self._down_until = time.time() + self.breaker_s
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            self._down_until = time.time() + self.breaker_s
            raise SigDown(type(exc).__name__) from exc
        finally:
            if self.client is None:
                await client.aclose()

    async def context(self, question: str, max_chars: int = 4000) -> str:
        (res,) = await self._batch([("stobox_answer_context",
                                     {"query": scrub(question), "max_chars": max_chars})])
        return json.dumps(res, ensure_ascii=False)[: max_chars + 2000]

    async def fact_check(self, claim: str) -> FactCheck:
        (res,) = await self._batch([("stobox_fact_check", {"claim": claim[:4000]})])
        return FactCheck(verdict=res.get("verdict", "unverifiable"),
                         issues=res.get("issues") or [], correction=res.get("correction"))
