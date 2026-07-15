from __future__ import annotations

import asyncio

from app.config import get_settings
from app.services.coverage import assess_evidence_coverage
from app.services.evidence_selector import select_evidence
from app.services.retrieval import hybrid_search


QUESTIONS = [
    "Can Simulink directly generate ROS 2 nodes in this knowledge base?",
    "What is the relationship between Simulink and AUTOSAR?",
    "What are fixed-step solver use cases?",
]


async def main() -> None:
    settings = get_settings()
    for question in QUESTIONS:
        trace: dict = {}
        candidates = await hybrid_search(question, limit=settings.evidence_candidate_k, use_rewrite=False, use_rerank=False, trace=trace)
        evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
        coverage = assess_evidence_coverage(question, evidence)
        print("\nQUESTION:", question)
        print({
            "passed": coverage.passed,
            "required_terms": coverage.required_terms,
            "covered_terms": coverage.covered_terms,
            "missing_terms": coverage.missing_terms,
            "ratio": coverage.coverage_ratio,
            "reason": coverage.reason,
            "selected": [item["chunk_id"] for item in evidence],
            "domains": trace.get("evidence_selector", {}).get("selected_domains"),
        })


if __name__ == "__main__":
    asyncio.run(main())
