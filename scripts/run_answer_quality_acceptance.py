from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter

from app.config import get_settings
from app.services.coverage import assess_evidence_coverage, insufficient_coverage_answer
from app.services.evidence_selector import select_evidence
from app.services.evidence_snippets import answer_generation_budget
from app.services.ollama import OllamaClient
from app.services.qa import build_answer_prompt, ensure_evidence_citations
from app.services.retrieval import hybrid_search


CASES = [
    {
        "id": "qa-01",
        "question": "Simulink 和 AUTOSAR 的关系是什么？",
        "kind": "answerable",
        "must_include": ["AUTOSAR"],
    },
    {
        "id": "qa-02",
        "question": "Stateflow 是什么？",
        "kind": "answerable",
        "must_include": ["Stateflow"],
    },
    {
        "id": "qa-03",
        "question": "固定步长求解器适合什么场景？",
        "kind": "answerable",
        "must_include": ["固定步长"],
    },
    {
        "id": "qa-04",
        "question": "ARXML 导入 Simulink 后通常会形成什么模型或组件？",
        "kind": "answerable",
        "must_include": ["ARXML", "Simulink"],
    },
    {
        "id": "qa-05",
        "question": "Simulink 可以直接生成 ROS 2 节点吗？",
        "kind": "refusal",
        "must_include": ["证据"],
    },
]


def _chinese_ratio(value: str) -> float:
    letters = re.findall(r"[A-Za-z]+", value)
    chinese = re.findall(r"[\u4e00-\u9fff]", value)
    total = len(chinese) + sum(len(item) for item in letters)
    if total == 0:
        return 0.0
    return len(chinese) / total


def _quality_flags(case: dict, answer: str, citations: list[dict], coverage_passed: bool) -> tuple[bool, list[str]]:
    flags: list[str] = []
    if case["kind"] == "refusal":
        if coverage_passed:
            flags.append("expected_refusal_but_coverage_passed")
        if not any(word in answer for word in ("证据不足", "没有足够证据", "无法可靠回答")):
            flags.append("refusal_wording_missing")
    else:
        if not coverage_passed:
            flags.append("coverage_failed_for_answerable")
        if not citations:
            flags.append("missing_citations")
        if _chinese_ratio(answer) < 0.25:
            flags.append("mostly_not_chinese")
        for term in case.get("must_include", []):
            if term.lower() not in answer.lower():
                flags.append(f"missing_expected_term:{term}")
    return not flags, flags


async def run_one(case: dict) -> dict:
    settings = get_settings()
    started = perf_counter()
    trace: dict = {}
    candidates = await hybrid_search(
        case["question"],
        limit=settings.evidence_candidate_k,
        use_rewrite=False,
        use_rerank=True,
        trace=trace,
    )
    evidence = select_evidence(case["question"], candidates, final_limit=settings.evidence_final_k, trace=trace)
    retrieval_ms = round((perf_counter() - started) * 1000)
    coverage = assess_evidence_coverage(case["question"], evidence)

    if not coverage.passed:
        answer = insufficient_coverage_answer(coverage)
        citations: list[dict] = []
        generation_ms = 0
        context = ""
    else:
        prompt, context = build_answer_prompt(case["question"], evidence, [], [], trace=trace)
        generation_started = perf_counter()
        answer = await OllamaClient().generate(prompt, num_predict=answer_generation_budget(case["question"]))
        generation_ms = round((perf_counter() - generation_started) * 1000)
        answer, citations = ensure_evidence_citations(answer, evidence)

    passed, flags = _quality_flags(case, answer, citations, coverage.passed)
    return {
        "id": case["id"],
        "question": case["question"],
        "kind": case["kind"],
        "passed": passed,
        "flags": flags,
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "total_ms": round((perf_counter() - started) * 1000),
        "coverage": {
            "passed": coverage.passed,
            "required_terms": coverage.required_terms,
            "covered_terms": coverage.covered_terms,
            "missing_terms": coverage.missing_terms,
            "reason": coverage.reason,
        },
        "trace": {
            "dense_skipped": trace.get("dense_skipped"),
            "dense_skip_reason": trace.get("dense_skip_reason"),
            "rerank_used": trace.get("rerank_used"),
            "rerank_reason": trace.get("rerank_reason"),
            "prompt_compaction": trace.get("prompt_compaction"),
        },
        "citations": [item.get("chunk_id") for item in citations],
        "chinese_ratio": round(_chinese_ratio(answer), 3),
        "context_chars": len(context),
        "answer": answer,
    }


def write_report(rows: list[dict]) -> Path:
    output = Path("/app/knowledge/evaluations/ANSWER_QUALITY_ACCEPTANCE.md")
    passed_count = sum(1 for row in rows if row["passed"])
    lines = [
        "# Answer Quality Acceptance",
        "",
        f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Cases: {len(rows)}",
        f"- Passed: {passed_count}/{len(rows)}",
        "",
        "| ID | Result | Retrieval ms | Generation ms | Total ms | Citations | Flags |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        result = "PASS" if row["passed"] else "REVIEW"
        lines.append(
            f"| {row['id']} | {result} | {row['retrieval_ms']} | {row['generation_ms']} | "
            f"{row['total_ms']} | {row['citations']} | {', '.join(row['flags']) or '-'} |"
        )
    lines.extend(["", "## Details", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']} - {row['question']}",
            "",
            f"- Passed: {row['passed']}",
            f"- Flags: {row['flags'] or '-'}",
            f"- Coverage: `{json.dumps(row['coverage'], ensure_ascii=False)}`",
            f"- Trace: `{json.dumps(row['trace'], ensure_ascii=False)}`",
            f"- Chinese ratio: {row['chinese_ratio']}",
            "",
            row["answer"].strip(),
            "",
        ])
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


async def main() -> None:
    rows = []
    for case in CASES:
        print(f"RUN {case['id']} {case['question']}", flush=True)
        row = await run_one(case)
        print(json.dumps({
            "id": row["id"],
            "passed": row["passed"],
            "flags": row["flags"],
            "retrieval_ms": row["retrieval_ms"],
            "generation_ms": row["generation_ms"],
            "citations": row["citations"],
            "chinese_ratio": row["chinese_ratio"],
        }, ensure_ascii=False), flush=True)
        rows.append(row)
    output = write_report(rows)
    print(f"REPORT={output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
