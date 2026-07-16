from __future__ import annotations

import asyncio
import json
from time import perf_counter

from app.config import get_settings
from app.services.coverage import assess_evidence_coverage
from app.services.evidence_selector import select_evidence
from app.services.evidence_snippets import answer_generation_budget
from app.services.ollama import OllamaClient
from app.services.qa import build_answer_prompt, ensure_evidence_citations
from app.services.retrieval import hybrid_search


QUESTION = "固定步长求解器适合什么场景？"


async def main() -> None:
    settings = get_settings()
    started = perf_counter()
    trace: dict = {}
    candidates = await hybrid_search(
        QUESTION,
        limit=settings.evidence_candidate_k,
        use_rewrite=False,
        use_rerank=True,
        trace=trace,
    )
    evidence = select_evidence(QUESTION, candidates, final_limit=settings.evidence_final_k, trace=trace)
    retrieval_ms = round((perf_counter() - started) * 1000)
    coverage = assess_evidence_coverage(QUESTION, evidence)
    prompt, context = build_answer_prompt(QUESTION, evidence, [], [], trace=trace)
    generation_started = perf_counter()
    answer = await OllamaClient().generate(prompt, num_predict=answer_generation_budget(QUESTION))
    generation_ms = round((perf_counter() - generation_started) * 1000)
    answer, citations = ensure_evidence_citations(answer, evidence)
    print(json.dumps({
        "question": QUESTION,
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "total_ms": round((perf_counter() - started) * 1000),
        "coverage_passed": coverage.passed,
        "dense_skipped": trace.get("dense_skipped"),
        "dense_skip_reason": trace.get("dense_skip_reason"),
        "rerank_used": trace.get("rerank_used"),
        "rerank_reason": trace.get("rerank_reason"),
        "context_chars": len(context),
        "citations": [item.get("chunk_id") for item in citations],
        "answer_preview": answer[:500],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
