from __future__ import annotations

import asyncio
import json

from app.config import get_settings
from app.services.coverage import assess_evidence_coverage
from app.services.evidence_selector import select_evidence
from app.services.evidence_snippets import answer_generation_budget
from app.services.qa import build_answer_prompt
from app.services.retrieval import hybrid_search


QUESTIONS = [
    "What is the relationship between Simulink and AUTOSAR?",
    "What are fixed-step solver use cases?",
    "Can Simulink directly generate ROS 2 nodes in this knowledge base?",
]


async def main() -> None:
    settings = get_settings()
    rows: list[dict] = []
    for question in QUESTIONS:
        trace: dict = {}
        candidates = await hybrid_search(
            question,
            limit=settings.evidence_candidate_k,
            use_rewrite=False,
            use_rerank=False,
            trace=trace,
        )
        evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
        coverage = assess_evidence_coverage(question, evidence)
        prompt = ""
        context = ""
        if coverage.passed:
            prompt, context = build_answer_prompt(question, evidence, [], [], trace=trace)
        rows.append({
            "question": question,
            "candidates": len(candidates),
            "selected": len(evidence),
            "coverage_passed": coverage.passed,
            "missing_terms": coverage.missing_terms,
            "budget": answer_generation_budget(question),
            "prompt_chars": len(prompt),
            "context_chars": len(context),
            "prompt_compaction": trace.get("prompt_compaction"),
        })
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
