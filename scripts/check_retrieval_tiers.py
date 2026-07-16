from __future__ import annotations

import asyncio
import json
import sys
from time import perf_counter

from app.config import get_settings
from app.services.retrieval import hybrid_search


DEFAULT_QUESTIONS = [
    "Stateflow是什么",
    "Simulink和AUTOSAR的关系",
    "如何把AUTOSAR ARXML导入Simulink",
    "固定步长和可变步长求解器有什么区别",
]


async def main() -> None:
    settings = get_settings()
    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else DEFAULT_QUESTIONS
    results: list[dict] = []
    for question in questions:
        started = perf_counter()
        trace: dict = {}
        candidates = await hybrid_search(
            question,
            limit=settings.evidence_candidate_k,
            use_rewrite=False,
            use_rerank=True,
            trace=trace,
        )
        decision = trace.get("retrieval_decision", {})
        diversity = trace.get("candidate_diversity", {})
        results.append({
            "question": question,
            "tier": decision.get("tier"),
            "mode": decision.get("mode"),
            "confidence": decision.get("confidence"),
            "reasons": decision.get("reasons"),
            "signals": decision.get("signals"),
            "duplicate_count": diversity.get("duplicate_count"),
            "duplicate_ratio": diversity.get("duplicate_ratio"),
            "rerank_used": trace.get("rerank_used"),
            "rerank_reason": trace.get("rerank_reason"),
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "retrieval_ms": trace.get("retrieval_ms"),
            "dense_ms": trace.get("dense_ms"),
            "graph_ms": trace.get("graph_ms"),
            "top_chunk_ids": [item.get("chunk_id") for item in candidates[:5]],
        })
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
