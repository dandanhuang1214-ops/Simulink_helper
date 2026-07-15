from __future__ import annotations

import asyncio
import json

from app.config import get_settings
from app.services.evidence_selector import select_evidence
from app.services.retrieval import hybrid_search


QUESTIONS = [
    "relationship between Simulink and AUTOSAR",
    "relationship between Stateflow and Simulink",
    "fixed-step solver use cases",
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
        print(f"\nQUESTION: {question}")
        print("candidate_count:", len(candidates), "selected_count:", len(selected))
        print("selector:", json.dumps(trace.get("evidence_selector", {}), ensure_ascii=False)[:1200])
        print("SELECTED:")
        for row in selected:
            print(
                " -",
                row["chunk_id"],
                row["title"],
                row.get("channels"),
                row.get("document_domains"),
                "selector_score=",
                row.get("selector_score"),
            )


if __name__ == "__main__":
    asyncio.run(main())
