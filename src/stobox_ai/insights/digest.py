"""Daily community digest.

Assembles a structured report from decision-log records (deterministic), then
optionally asks the reasoner for a short narrative summary. Renders to plain
text for Telegram/Slack or returns the dict for a dashboard/API.
"""

from __future__ import annotations

from typing import Any

from ..analytics.logger import Decision, DecisionLog
from ..llm.base import ChatMessage, LLMProvider
from ..logging import get_logger
from .analyzer import (
    cluster_questions,
    documentation_gaps,
    potential_leads,
    sentiment_proxy,
)

log = get_logger(__name__)


class DailyDigest:
    def __init__(self, decisions: DecisionLog, reasoner: LLMProvider | None = None) -> None:
        self.decisions = decisions
        self.reasoner = reasoner

    def build(self, last_n: int | None = None) -> dict[str, Any]:
        records: list[Decision] = self.decisions.records(last_n)
        if not records:
            return {"count": 0, "empty": True}

        clusters = cluster_questions(records)
        gaps = documentation_gaps(records)
        leads = potential_leads(records)
        mod_actions = [d for d in records if d.moderation != "none"]
        escalations = [d for d in records if d.escalated]
        langs = {}
        for d in records:
            langs[d.language] = langs.get(d.language, 0) + 1

        return {
            "count": len(records),
            "top_questions": [
                {"question": c.representative, "asked": c.count, "topics": c.topics}
                for c in clusters[:8]
            ],
            "documentation_gaps": [
                {
                    "question": c.representative,
                    "asked": c.count,
                    "unresolved": c.unresolved,
                    "avg_confidence": c.avg_confidence,
                }
                for c in gaps[:8]
            ],
            "potential_leads": leads[:10],
            "moderation_actions": [
                {"category": d.meta.get("category"), "action": d.moderation} for d in mod_actions
            ][:20],
            "escalations": len(escalations),
            "sentiment": sentiment_proxy(records),
            "languages": sorted(langs.items(), key=lambda x: x[1], reverse=True),
            "metrics": self.decisions.snapshot(last_n),
        }

    async def narrative(self, digest: dict[str, Any]) -> str | None:
        """Optional 3–4 sentence exec summary of the digest."""
        if not self.reasoner or digest.get("empty"):
            return None
        gaps = ", ".join(g["question"] for g in digest.get("documentation_gaps", [])[:3]) or "none"
        top = ", ".join(q["question"] for q in digest.get("top_questions", [])[:3]) or "none"
        msg = [
            ChatMessage(
                "system",
                "You are a community analytics assistant. Summarize the day for a "
                "Stobox admin in 3–4 factual sentences. No fluff, no invented data.",
            ),
            ChatMessage(
                "user",
                f"Messages: {digest['count']}. Top questions: {top}. "
                f"Documentation gaps (asked but low confidence): {gaps}. "
                f"Potential leads: {len(digest.get('potential_leads', []))}. "
                f"Community health: {digest['sentiment']['label']} "
                f"({digest['sentiment']['health_score']}). "
                f"Escalations: {digest['escalations']}.",
            ),
        ]
        try:
            text = (await self.reasoner.complete(msg, temperature=0.2, max_tokens=200)).text
            from ..guardrails import ComplianceRails

            rail = ComplianceRails().post_process(text, "")
            return None if rail.blocked else rail.text
        except Exception as exc:  # noqa: BLE001
            log.warning("digest.narrative_failed", error=str(exc))
            return None

    @staticmethod
    def render_text(digest: dict[str, Any], narrative: str | None = None) -> str:
        if digest.get("empty"):
            return "🗞️ Daily digest: no activity recorded yet."
        lines = ["🗞️ *Stobox Daily Community Digest*"]
        if narrative:
            lines += ["", narrative, ""]
        s = digest["sentiment"]
        lines.append(
            f"Messages: {digest['count']} · Health: {s['label']} ({s['health_score']}) · "
            f"Escalations: {digest['escalations']}"
        )
        if digest["top_questions"]:
            lines.append("\n*Top questions:*")
            lines += [f"• {q['question']} ({q['asked']}×)" for q in digest["top_questions"][:5]]
        if digest["documentation_gaps"]:
            lines.append("\n⚠️ *Documentation gaps* (asked, low confidence):")
            lines += [
                f"• {g['question']} ({g['asked']}× · conf {g['avg_confidence']})"
                for g in digest["documentation_gaps"][:5]
            ]
        if digest["potential_leads"]:
            lines.append(f"\n💼 Potential leads: {len(digest['potential_leads'])}")
            lines += [
                f"• {lead['user_key']} ({lead['touches']} touches"
                + (", ✅ captured" if lead["captured"] else "") + ")"
                for lead in digest["potential_leads"][:5]
            ]
        if digest["moderation_actions"]:
            lines.append(f"\n🛡️ Moderation actions: {len(digest['moderation_actions'])}")
        return "\n".join(lines)
