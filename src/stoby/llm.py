"""The one model call: claude-sonnet-5 answers from the source blocks.

The behaviour prompt is static and cached; the volatile source blocks and the
question go in the user turn after the cache breakpoint. Adaptive thinking at
low effort: with thinking off, Sonnet 5 made factual slips on STBU in the
27.09.2026 probe, and reasoning needs room inside max_tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anthropic

from .ledger import cost_usd

PROMPT_PATH = Path(__file__).resolve().parents[2] / "config" / "prompts" / "stoby.md"


class ModelDown(Exception):
    pass


@dataclass
class Draft:
    text: str
    usd: float
    input_tokens: int
    output_tokens: int
    cache_read: int
    stop_reason: str


def load_prompt(path: Path = PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")


class Answerer:
    def __init__(self, model: str = "claude-sonnet-5", effort: str = "low",
                 client: anthropic.AsyncAnthropic | None = None,
                 prompt: str | None = None) -> None:
        self.model, self.effort = model, effort
        self.client = client or anthropic.AsyncAnthropic(max_retries=2, timeout=60)
        self.prompt = prompt or load_prompt()

    async def draft(self, sources: str, question: str, correction: str | None = None,
                    site_block: str = "") -> Draft:
        """`site_block` is cached as a second system block: it changes at most
        every ten minutes, so repeated answers read it at a tenth of the price.
        `sources` carries what is not cached (the SIG context)."""
        user = f"{sources}\n\n[QUESTION]\n{question}"
        if correction:
            user += (f"\n\n[CORRECTION]\nYour previous draft was rejected: {correction}\n"
                     "Answer again using only the sources.")
        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=[{"type": "text", "text": self.prompt,
                         "cache_control": {"type": "ephemeral"}},
                        *([{"type": "text", "text": site_block,
                            "cache_control": {"type": "ephemeral"}}] if site_block else [])],
                messages=[{"role": "user", "content": user}],
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError,
                anthropic.APIStatusError) as exc:
            raise ModelDown(type(exc).__name__) from exc
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        u = resp.usage
        return Draft(text=text, usd=cost_usd(self.model, u), input_tokens=u.input_tokens,
                     output_tokens=u.output_tokens,
                     cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                     stop_reason=resp.stop_reason or "")
