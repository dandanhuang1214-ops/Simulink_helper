from __future__ import annotations

import asyncio
import json
from time import perf_counter

from app.config import get_settings
from app.services.evidence_selector import select_evidence
from app.services.evidence_snippets import answer_generation_budget
from app.services.ollama import OllamaClient
from app.services.qa import build_answer_prompt, ensure_evidence_citations
from app.services.retrieval import hybrid_search


QUESTION = "What is the relationship between Simulink and AUTOSAR?"


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
    prompt, context = build_answer_prompt(QUESTION, evidence, [], [], trace=trace)
    budget = answer_generation_budget(QUESTION)
    generation_started = perf_counter()
    answer = await OllamaClient().generate(prompt, num_predict=budget)
    generation_ms = round((perf_counter() - generation_started) * 1000)
    answer, citations = ensure_evidence_citations(answer, evidence)
    print(json.dumps({
        "question": QUESTION,
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "total_ms": round((perf_counter() - started) * 1000),
        "budget": budget,
        "context_chars": len(context),
        "prompt_chars": len(prompt),
        "prompt_compaction": trace.get("prompt_compaction"),
        "citations": [item.get("chunk_id") for item in citations],
        "answer_preview": answer[:600],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
