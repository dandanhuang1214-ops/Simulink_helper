from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from time import perf_counter

from app.config import get_settings
from app.services.conversations import active_memories
from app.services.evidence_selector import select_evidence
from app.services.ollama import OllamaClient
from app.services.qa import build_answer_prompt, ensure_evidence_citations, judge_answer
from app.services.retrieval import hybrid_search


QUESTIONS = [
    ("r3g-q01", "What is the relationship between Simulink and AUTOSAR?"),
    ("r3g-q02", "What are fixed-step solver use cases?"),
]


async def run_one(question_id: str, question: str) -> dict:
    settings = get_settings()
    started = perf_counter()
    trace: dict = {}
    candidates = await hybrid_search(question, limit=settings.evidence_candidate_k, use_rewrite=False, use_rerank=True, trace=trace)
    evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
    retrieval_ms = round((perf_counter() - started) * 1000)
    prompt, context = build_answer_prompt(question, evidence, [], active_memories())
    generation_started = perf_counter()
    answer = await OllamaClient().generate(prompt, num_predict=360)
    generation_ms = round((perf_counter() - generation_started) * 1000)
    answer, citations = ensure_evidence_citations(answer, evidence)
    evaluation = await judge_answer(question, answer, context)
    total_ms = round((perf_counter() - started) * 1000)
    selector = trace.get("evidence_selector", {})
    return {
        "id": question_id,
        "question": question,
        "latency_ms": total_ms,
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "answer": answer,
        "citation_ids": [row.get("chunk_id") for row in citations],
        "selected_domains": selector.get("selected_domains", []),
        "selected": selector.get("selected", []),
        "evaluation": evaluation,
    }


def write_report(rows: list[dict]) -> Path:
    output = Path("/app/knowledge/evaluations/GENERATION_SPOTCHECK_ROUND3.md")
    lines = [
        "# Generation Spotcheck Round 3",
        "",
        f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Questions: {len(rows)}",
        "",
        "| ID | Latency ms | Retrieval ms | Generation ms | Domains | Citations | Passed |",
        "|---|---:|---:|---:|---|---|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['latency_ms']} | {row['retrieval_ms']} | {row['generation_ms']} | "
            f"{', '.join(row['selected_domains'])} | {row['citation_ids']} | {row['evaluation'].get('passed')} |"
        )
    lines.extend(["", "## Details", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']} - {row['question']}",
            "",
            f"- Latency: {row['latency_ms']} ms",
            f"- Citations: {row['citation_ids']}",
            f"- Evaluation: {row['evaluation']}",
            "",
            "Selected evidence:",
            "",
        ])
        for item in row["selected"]:
            lines.append(f"- E:{item.get('chunk_id')} | {item.get('title')} | {item.get('channels')} | {item.get('document_domains')}")
        lines.extend(["", "Answer:", "", row["answer"].strip(), ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


async def main() -> None:
    rows = []
    for question_id, question in QUESTIONS:
        print(f"Running {question_id}: {question}", flush=True)
        row = await run_one(question_id, question)
        print(
            {
                "id": row["id"],
                "latency_ms": row["latency_ms"],
                "generation_ms": row["generation_ms"],
                "citations": row["citation_ids"],
                "passed": row["evaluation"].get("passed"),
            },
            flush=True,
        )
        rows.append(row)
    output = write_report(rows)
    print(f"REPORT={output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
