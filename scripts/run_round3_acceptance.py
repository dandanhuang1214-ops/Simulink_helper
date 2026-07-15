from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter

from app.config import get_settings
from app.services.conversations import active_memories
from app.services.evidence_selector import select_evidence
from app.services.ollama import OllamaClient
from app.services.qa import build_answer_prompt, ensure_evidence_citations, judge_answer
from app.services.retrieval import _is_simple_relation_query, hybrid_search


QUESTIONS = [
    {
        "id": "r3-q01",
        "type": "relationship",
        "question": "What is the relationship between Simulink and AUTOSAR?",
        "expect": "Should cite both AUTOSAR-side and Simulink-side evidence.",
    },
    {
        "id": "r3-q02",
        "type": "relationship",
        "question": "What is the relationship between Stateflow and Simulink?",
        "expect": "Should cite Stateflow evidence and at least one Simulink-side evidence chunk.",
    },
    {
        "id": "r3-q03",
        "type": "mapping",
        "question": "How does an AUTOSAR composition map to a Simulink model?",
        "expect": "Should focus on AUTOSAR composition import/modeling evidence.",
    },
    {
        "id": "r3-q04",
        "type": "single-domain",
        "question": "What are fixed-step solver use cases?",
        "expect": "Should stay in solver/Simulink evidence and avoid Stateflow/AUTOSAR noise.",
    },
    {
        "id": "r3-q05",
        "type": "definition",
        "question": "What is Stateflow?",
        "expect": "Should define Stateflow using Stateflow evidence.",
    },
    {
        "id": "r3-q06",
        "type": "no-answer",
        "question": "Can Simulink directly generate ROS 2 nodes in this knowledge base?",
        "expect": "Should refuse or clearly say evidence is insufficient if no imported evidence supports it.",
    },
]


def _answer_status(answer: str, citations: list[dict], evidence: list[dict], evaluation: dict) -> str:
    if not evidence:
        return "NO_EVIDENCE"
    if "insufficient" in answer.lower() or "not enough" in answer.lower() or "no evidence" in answer.lower():
        return "REFUSED"
    if citations and evaluation.get("passed"):
        return "PASS"
    if citations:
        return "REVIEW"
    return "FAIL"


async def run_one(item: dict) -> dict:
    settings = get_settings()
    started = perf_counter()
    trace: dict = {}
    last_error: Exception | None = None
    candidates = []
    for attempt in range(2):
        try:
            candidates = await hybrid_search(
                item["question"],
                limit=settings.evidence_candidate_k,
                use_rewrite=False,
                use_rerank=True,
                trace=trace,
            )
            break
        except Exception as exc:  # smoke acceptance should continue on transient infra failures
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(3)
            else:
                raise
    evidence = select_evidence(
        item["question"],
        candidates,
        final_limit=settings.evidence_final_k,
        trace=trace,
    )
    retrieval_ms = round((perf_counter() - started) * 1000)

    if not evidence:
        answer = "The current knowledge base does not contain enough evidence to answer this question reliably."
        citations: list[dict] = []
        evaluation = {"passed": False, "reason": "retrieval_empty"}
        context = ""
    else:
        prompt, context = build_answer_prompt(item["question"], evidence, [], active_memories())
        generation_started = perf_counter()
        generation_tokens = 360 if _is_simple_relation_query(item["question"]) else 520
        answer = await OllamaClient().generate(prompt, num_predict=generation_tokens)
        generation_ms = round((perf_counter() - generation_started) * 1000)
        trace["generation_ms"] = generation_ms
        answer, citations = ensure_evidence_citations(answer, evidence)
        evaluation = await judge_answer(item["question"], answer, context)
        evaluation["passed"] = bool(evaluation.get("passed"))

    total_ms = round((perf_counter() - started) * 1000)
    selector = trace.get("evidence_selector", {})
    return {
        **item,
        "status": _answer_status(answer, citations, evidence, evaluation),
        "latency_ms": total_ms,
        "retrieval_ms": retrieval_ms,
        "answer": answer,
        "citations": citations,
        "citation_ids": [row.get("chunk_id") for row in citations],
        "selected": selector.get("selected", []),
        "selected_domains": selector.get("selected_domains", []),
        "question_role": selector.get("question_role"),
        "trace_summary": {
            "candidate_count": selector.get("candidate_count"),
            "selected_count": selector.get("selected_count"),
            "wiki_used": bool(trace.get("wiki")),
            "graph_used": bool((trace.get("graph") or {}).get("used")),
            "dense_ms": trace.get("dense_ms"),
            "bm25_ms": trace.get("bm25_ms"),
            "wiki_ms": trace.get("wiki_ms"),
            "graph_ms": trace.get("graph_ms"),
            "rerank_used": trace.get("rerank_used"),
            "rerank_reason": trace.get("rerank_reason"),
        },
        "evaluation": evaluation,
        "error": str(last_error) if last_error else None,
    }


def _clip(value: str, limit: int = 900) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[:limit].rstrip() + "..."


def write_report(results: list[dict]) -> Path:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output = Path("/app/knowledge/evaluations/ANSWER_ACCEPTANCE_ROUND3_WIKI_GRAPH_SELECTOR.md")
    passed = sum(1 for row in results if row["status"] == "PASS")
    review = sum(1 for row in results if row["status"] == "REVIEW")
    refused = sum(1 for row in results if row["status"] == "REFUSED")
    failed = sum(1 for row in results if row["status"] in {"FAIL", "NO_EVIDENCE"})

    lines = [
        "# Answer Acceptance Round 3 - Wiki-first + Graph + Evidence Selector",
        "",
        f"- Time: {now}",
        f"- Questions: {len(results)}",
        f"- PASS: {passed}",
        f"- REVIEW: {review}",
        f"- REFUSED: {refused}",
        f"- FAIL/NO_EVIDENCE: {failed}",
        "",
        "## Summary Table",
        "",
        "| ID | Type | Status | Latency ms | Role | Domains | Citations | Trace |",
        "|---|---|---:|---:|---|---|---|---|",
    ]
    for row in results:
        trace = row["trace_summary"]
        lines.append(
            f"| {row['id']} | {row['type']} | {row['status']} | {row['latency_ms']} | "
            f"{row.get('question_role')} | {', '.join(row.get('selected_domains') or [])} | "
            f"{row.get('citation_ids')} | "
            f"wiki={trace.get('wiki_used')}, graph={trace.get('graph_used')}, rerank={trace.get('rerank_used')} |"
        )
    lines.extend(["", "## Details", ""])
    for row in results:
        lines.extend([
            f"### {row['id']} - {row['question']}",
            "",
            f"- Type: {row['type']}",
            f"- Expectation: {row['expect']}",
            f"- Status: {row['status']}",
            f"- Latency: {row['latency_ms']} ms",
            f"- Selected domains: {row.get('selected_domains')}",
            f"- Citations: {row.get('citation_ids')}",
            f"- Trace: `{json.dumps(row['trace_summary'], ensure_ascii=False)}`",
            "",
            "Selected evidence:",
            "",
        ])
        for selected in row.get("selected") or []:
            lines.append(
                f"- E:{selected.get('chunk_id')} | {selected.get('title')} | "
                f"{selected.get('channels')} | {selected.get('document_domains')} | "
                f"role={selected.get('evidence_role')} | score={selected.get('selector_score')}"
            )
        lines.extend(["", "Answer excerpt:", "", _clip(row["answer"]), ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


async def main() -> None:
    results = []
    for item in QUESTIONS:
        print(f"Running {item['id']}: {item['question']}", flush=True)
        try:
            result = await run_one(item)
        except Exception as exc:
            result = {
                **item,
                "status": "ERROR",
                "latency_ms": 0,
                "retrieval_ms": 0,
                "answer": "",
                "citations": [],
                "citation_ids": [],
                "selected": [],
                "selected_domains": [],
                "question_role": None,
                "trace_summary": {},
                "evaluation": {"passed": False, "reason": f"{type(exc).__name__}: {exc}"},
                "error": f"{type(exc).__name__}: {exc}",
            }
        print(
            {
                "id": result["id"],
                "status": result["status"],
                "latency_ms": result["latency_ms"],
                "citations": result["citation_ids"],
                "domains": result["selected_domains"],
            },
            flush=True,
        )
        results.append(result)
    output = write_report(results)
    print(f"REPORT={output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
