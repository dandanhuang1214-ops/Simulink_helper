from __future__ import annotations

import asyncio
import json

from app.services.retrieval import hybrid_search


QUESTIONS = [
    "relationship between Simulink and AUTOSAR",
    "relationship between Stateflow and Simulink",
    "fixed-step solver use cases",
]


async def main() -> None:
    for question in QUESTIONS:
        trace: dict = {}
        rows = await hybrid_search(question, limit=6, use_rewrite=False, use_rerank=False, trace=trace)
        print(f"\nQUESTION: {question}")
        print("GRAPH:", json.dumps(trace.get("graph", {}), ensure_ascii=False)[:1000])
        print("RESULTS:")
        for row in rows:
            print(
                " -",
                row["chunk_id"],
                row["title"],
                row.get("channels"),
                "score=",
                round(row.get("final_score", 0), 5),
            )


if __name__ == "__main__":
    asyncio.run(main())
