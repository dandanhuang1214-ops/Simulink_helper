from __future__ import annotations

import asyncio
import json
import sys

from app.config import get_settings
from app.services.evidence_selector import select_evidence
from app.services.retrieval import hybrid_search


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "ARXML 导入 Simulink 后通常会形成什么模型或组件？"
    settings = get_settings()
    trace: dict = {}
    candidates = await hybrid_search(
        question,
        limit=settings.evidence_candidate_k,
        use_rewrite=False,
        use_rerank=True,
        trace=trace,
    )
    evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
    print(json.dumps({
        "question": question,
        "trace": {
            "queries": trace.get("queries"),
            "dense_skipped": trace.get("dense_skipped"),
            "dense_skip_reason": trace.get("dense_skip_reason"),
            "rerank_reason": trace.get("rerank_reason"),
            "selector": trace.get("evidence_selector"),
        },
        "selected": [
            {
                "chunk_id": item.get("chunk_id"),
                "title": item.get("title"),
                "document_domains": item.get("document_domains"),
                "channels": item.get("channels"),
                "heading": item.get("heading_path"),
                "content": (item.get("content") or "")[:500],
            }
            for item in evidence
        ],
        "top_candidates": [
            {
                "chunk_id": item.get("chunk_id"),
                "title": item.get("title"),
                "document_domains": item.get("document_domains"),
                "channels": item.get("channels"),
                "heading": item.get("heading_path"),
                "score": item.get("final_score"),
                "content": (item.get("content") or "")[:240],
            }
            for item in candidates[:12]
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
