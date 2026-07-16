"""Weekly FAQ generator.

Clusters the week's recurring questions, then for each top cluster retrieves
documentation and generates a concise, grounded, cited answer. Clusters that
can't be answered from the docs are returned as ``needs_docs`` entries — the
raw material for closing documentation gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..analytics.logger import DecisionLog
from ..knowledge.retrieval import HybridRetriever
from ..llm.base import ChatMessage, LLMProvider
from ..logging import get_logger
from ..prompts import PromptLibrary
from .analyzer import cluster_questions

log = get_logger(__name__)


@dataclass(slots=True)
class FAQEntry:
    question: str
    answer: str
    frequency: int
    citations: list[str] = field(default_factory=list)
    needs_docs: bool = False


class WeeklyFAQ:
    def __init__(
        self,
        decisions: DecisionLog,
        retriever: HybridRetriever,
        reasoner: LLMProvider,
        prompts: PromptLibrary,
        confidence_threshold: float = 0.55,
    ) -> None:
        self.decisions = decisions
        self.retriever = retriever
        self.reasoner = reasoner
        self.prompts = prompts
        self.threshold = confidence_threshold

    async def generate(self, *, top_n: int = 10, last_n: int | None = None) -> list[FAQEntry]:
        clusters = cluster_questions(self.decisions.records(last_n))[:top_n]
        entries: list[FAQEntry] = []
        for cluster in clusters:
            entries.append(await self._answer_cluster(cluster.representative, cluster.count))
        log.info("faq.generated", entries=len(entries),
                 gaps=sum(1 for e in entries if e.needs_docs))
        return entries

    async def _answer_cluster(self, question: str, frequency: int) -> FAQEntry:
        from ..agents.confidence import top_relevance

        retrieved = await self.retriever.retrieve(question)
        # Gap detection needs ABSOLUTE relevance — the fused score is normalized
        # to ~1.0 whenever anything is retrieved, which under-reported doc gaps.
        semantic = getattr(self.retriever.embedder, "name", "") != "local-hash"
        top = top_relevance(retrieved, semantic_embeddings=semantic)
        if not retrieved or top < self.threshold:
            return FAQEntry(
                question=question,
                answer="(No supporting documentation found — candidate for new docs.)",
                frequency=frequency,
                needs_docs=True,
            )
        context_blocks, citations = [], []
        for rc in retrieved[:4]:
            title = rc.chunk.meta.title if rc.chunk.meta else "Stobox docs"
            label = title + (f" §{rc.chunk.section}" if rc.chunk.section else "")
            context_blocks.append(f"[{label}]\n{rc.chunk.text}")
            if title not in citations:
                citations.append(title)
        prompt = self.prompts.render(
            "faq_generation", question=question, context="\n\n".join(context_blocks)
        )
        try:
            result = await self.reasoner.complete(
                [ChatMessage("user", prompt)], temperature=0.2, max_tokens=350
            )
            answer = result.text.strip()
            # FAQ output is designed for publication ⇒ run the compliance rails
            # (forbidden-claim block, impostor scrub, disclaimers). Stateless.
            from ..guardrails import ComplianceRails

            answer = ComplianceRails().post_process(answer, question).text
        except Exception as exc:  # noqa: BLE001
            log.warning("faq.answer_failed", error=str(exc))
            answer = "(Answer generation failed.)"
        return FAQEntry(
            question=question, answer=answer, frequency=frequency, citations=citations
        )

    @staticmethod
    def render_markdown(entries: list[FAQEntry]) -> str:
        answered = [e for e in entries if not e.needs_docs]
        gaps = [e for e in entries if e.needs_docs]
        lines = ["# Stobox Weekly FAQ", ""]
        for i, e in enumerate(answered, 1):
            lines.append(f"### {i}. {e.question}  _(asked {e.frequency}×)_")
            lines.append(e.answer)
            if e.citations:
                lines.append(f"\n*Sources: {', '.join(e.citations)}*")
            lines.append("")
        if gaps:
            lines.append("## ⚠️ Documentation gaps (write these next)")
            lines += [f"- {e.question} _(asked {e.frequency}×)_" for e in gaps]
        return "\n".join(lines)

    @staticmethod
    def to_dict(entries: list[FAQEntry]) -> list[dict[str, Any]]:
        return [
            {
                "question": e.question,
                "answer": e.answer,
                "frequency": e.frequency,
                "citations": e.citations,
                "needs_docs": e.needs_docs,
            }
            for e in entries
        ]
