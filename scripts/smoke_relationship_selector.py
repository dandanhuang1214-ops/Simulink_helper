from __future__ import annotations

import asyncio
import json

from app.config import get_settings
from app.services.evidence_selector import select_evidence
from app.services.retrieval import hybrid_search


QUESTIONS = [
    "relationship between Simulink and AUTOSAR",
    "relationship between Stateflow and Simulink",
    "how AUTOSAR composition maps to Simulink model",
]


async def main() -> None:
    settings = get_settings()
    for question in QUESTIONS:
        trace: dict = {}
        candidates = await hybrid_search(
            question,
            limit=settings.evidence_candidate_k,
            use_rewrite=False,
            use_rerank=False,
            trace=trace,
        )
        selected = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
        selector = trace.get("evidence_selector", {})
        print(f"\nQUESTION: {question}")
        print(
            "role:",
            selector.get("question_role"),
            "candidate_count:",
            len(candidates),
            "selected_count:",
            len(selected),
            "selected_domains:",
            selector.get("selected_domains"),
        )
        print("wiki_pages:", json.dumps(trace.get("wiki", [])[:1], ensure_ascii=False)[:500])
        print("SELECTED:")
        for row in selected:
            print(
                " -",
                row["chunk_id"],
                row["title"],
                row.get("channels"),
                row.get("document_domains"),
                row.get("evidence_role"),
                "score=",
                row.get("selector_score"),
            )


if __name__ == "__main__":
    asyncio.run(main())
