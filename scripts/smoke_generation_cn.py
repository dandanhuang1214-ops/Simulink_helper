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


QUESTIONS = [
    "Simulink 和 AUTOSAR 的关系是什么？",
    "Stateflow 是什么？",
    "固定步长求解器适合什么场景？",
]


async def run_one(question: str) -> dict:
    settings = get_settings()
    started = perf_counter()
    trace: dict = {}
    candidates = await hybrid_search(
        question,
        limit=settings.evidence_candidate_k,
        use_rewrite=False,
        use_rerank=True,
        trace=trace,
    )
    evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
    retrieval_ms = round((perf_counter() - started) * 1000)
    coverage = assess_evidence_coverage(question, evidence)
    if not coverage.passed:
        return {
            "question": question,
            "retrieval_ms": retrieval_ms,
            "coverage_passed": False,
            "missing_terms": coverage.missing_terms,
            "selected": len(evidence),
        }

    prompt, context = build_answer_prompt(question, evidence, [], [], trace=trace)
    budget = answer_generation_budget(question)
    generation_started = perf_counter()
    answer = await OllamaClient().generate(prompt, num_predict=budget)
    generation_ms = round((perf_counter() - generation_started) * 1000)
    answer, citations = ensure_evidence_citations(answer, evidence)
    return {
        "question": question,
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "total_ms": round((perf_counter() - started) * 1000),
        "coverage_passed": True,
        "budget": budget,
        "context_chars": len(context),
        "prompt_chars": len(prompt),
        "selected": len(evidence),
        "prompt_compaction": trace.get("prompt_compaction"),
        "citations": [item.get("chunk_id") for item in citations],
        "answer_preview": answer[:500],
    }


async def main() -> None:
    rows = []
    for question in QUESTIONS:
        print(f"RUN {question}", flush=True)
        row = await run_one(question)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
        rows.append(row)
    print("SUMMARY=" + json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
