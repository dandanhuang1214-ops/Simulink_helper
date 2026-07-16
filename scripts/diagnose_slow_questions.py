from __future__ import annotations

import asyncio
import argparse
import json
import re
from time import perf_counter

from app.config import get_settings
from app.services.coverage import insufficient_coverage_answer
from app.services.evidence_snippets import answer_generation_budget
from app.services.ollama import OllamaClient
from app.services.qa import build_answer_prompt, ensure_evidence_citations
from app.services.retrieval_pipeline import retrieve_evidence_with_coverage


QUESTIONS = [
    ("Q001", "Stateflow 是什么？"),
    ("Q009", "Stateflow 和普通 Simulink blocks 在控制逻辑建模上有什么区别？"),
    ("Q012", "Simulink 模型生成代码前通常需要检查哪些配置？"),
]


def _chinese_ratio(value: str) -> float:
    letters = re.findall(r"[A-Za-z]+", value)
    chinese = re.findall(r"[\u4e00-\u9fff]", value)
    total = len(chinese) + sum(len(item) for item in letters)
    return 0.0 if total == 0 else len(chinese) / total


async def run_one(question_id: str, question: str, *, profile: str) -> dict:
    settings = get_settings()
    started = perf_counter()
    trace: dict = {}
    retrieval = await retrieve_evidence_with_coverage(
        question,
        use_rewrite=False,
        use_rerank=True,
        retrieval_profile=profile,
        trace=trace,
    )
    evidence = retrieval.evidence
    retrieval_ms = round((perf_counter() - started) * 1000)
    coverage = retrieval.coverage
    if not coverage.passed:
        answer = insufficient_coverage_answer(coverage)
        generation_ms = 0
        citations = []
        context = ""
    else:
        prompt, context = build_answer_prompt(question, evidence, [], [], trace=trace)
        generation_started = perf_counter()
        answer = await OllamaClient().generate(prompt, num_predict=answer_generation_budget(question))
        generation_ms = round((perf_counter() - generation_started) * 1000)
        answer, citations = ensure_evidence_citations(answer, evidence)
    return {
        "id": question_id,
        "question": question,
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "total_ms": round((perf_counter() - started) * 1000),
        "coverage_passed": coverage.passed,
        "budget": answer_generation_budget(question),
        "context_chars": len(context),
        "dense_skipped": trace.get("dense_skipped"),
        "dense_skip_reason": trace.get("dense_skip_reason"),
        "retrieval_profile": trace.get("retrieval_profile"),
        "rerank_used": trace.get("rerank_used"),
        "rerank_reason": trace.get("rerank_reason"),
        "prompt_compaction": trace.get("prompt_compaction"),
        "citations": [item.get("chunk_id") for item in citations],
        "chinese_ratio": round(_chinese_ratio(answer), 3),
        "answer_chars": len(answer),
        "answer_preview": answer[:500],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    args = parser.parse_args()
    for question_id, question in QUESTIONS:
        row = await run_one(question_id, question, profile=args.profile)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
