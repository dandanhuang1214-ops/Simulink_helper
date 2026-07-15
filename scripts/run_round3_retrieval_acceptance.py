from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from time import perf_counter

from app.config import get_settings
from app.services.evidence_selector import select_evidence
from app.services.retrieval import hybrid_search


QUESTIONS = [
    ("r3r-q01", "relationship", "What is the relationship between Simulink and AUTOSAR?"),
    ("r3r-q02", "relationship", "What is the relationship between Stateflow and Simulink?"),
    ("r3r-q03", "mapping", "How does an AUTOSAR composition map to a Simulink model?"),
    ("r3r-q04", "single-domain", "What are fixed-step solver use cases?"),
    ("r3r-q05", "definition", "What is Stateflow?"),
    ("r3r-q06", "no-answer", "Can Simulink directly generate ROS 2 nodes in this knowledge base?"),
]


async def run_one(question_id: str, kind: str, question: str) -> dict:
    settings = get_settings()
    started = perf_counter()
    trace: dict = {}
    candidates = await hybrid_search(
        question,
        limit=settings.evidence_candidate_k,
        use_rewrite=False,
        use_rerank=False,
        trace=trace,
    )
    selected = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
    selector = trace.get("evidence_selector", {})
    return {
        "id": question_id,
        "type": kind,
        "question": question,
        "latency_ms": round((perf_counter() - started) * 1000),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "question_role": selector.get("question_role"),
        "selected_domains": selector.get("selected_domains", []),
        "wiki_used": bool(trace.get("wiki")),
        "graph_used": bool((trace.get("graph") or {}).get("used")),
        "dense_ms": trace.get("dense_ms"),
        "bm25_ms": trace.get("bm25_ms"),
        "wiki_ms": trace.get("wiki_ms"),
        "graph_ms": trace.get("graph_ms"),
        "selected": selector.get("selected", []),
    }


def status_for(row: dict) -> str:
    domains = set(row.get("selected_domains") or [])
    if row["type"] == "relationship":
        if row["id"] == "r3r-q01":
            return "PASS" if {"autosar", "simulink"}.issubset(domains) else "REVIEW"
        if row["id"] == "r3r-q02":
            return "PASS" if {"stateflow", "simulink"}.issubset(domains) else "REVIEW"
    if row["type"] == "mapping":
        return "PASS" if "autosar" in domains else "REVIEW"
    if row["type"] == "single-domain":
        return "PASS" if domains <= {"simulink", "solver"} and "solver" in domains else "REVIEW"
    if row["type"] == "definition":
        return "PASS" if "stateflow" in domains else "REVIEW"
    if row["type"] == "no-answer":
        return "REVIEW"
    return "REVIEW"


def write_report(rows: list[dict]) -> Path:
    output = Path("/app/knowledge/evaluations/RETRIEVAL_ACCEPTANCE_ROUND3.md")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        row["status"] = status_for(row)
    lines = [
        "# Retrieval Acceptance Round 3",
        "",
        f"- Time: {now}",
        f"- Questions: {len(rows)}",
        f"- PASS: {sum(1 for row in rows if row['status'] == 'PASS')}",
        f"- REVIEW: {sum(1 for row in rows if row['status'] != 'PASS')}",
        "",
        "| ID | Type | Status | Latency ms | Role | Domains | Selected | Channels |",
        "|---|---|---:|---:|---|---|---:|---|",
    ]
    for row in rows:
        channels = sorted({channel for item in row.get("selected", []) for channel in item.get("channels", [])})
        lines.append(
            f"| {row['id']} | {row['type']} | {row['status']} | {row['latency_ms']} | "
            f"{row['question_role']} | {', '.join(row['selected_domains'])} | "
            f"{row['selected_count']} | {', '.join(channels)} |"
        )
    lines.extend(["", "## Details", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']} - {row['question']}",
            "",
            f"- Status: {row['status']}",
            f"- Latency: {row['latency_ms']} ms",
            f"- Trace: bm25={row['bm25_ms']}ms, dense={row['dense_ms']}ms, wiki={row['wiki_ms']}ms, graph={row['graph_ms']}ms",
            f"- Wiki used: {row['wiki_used']}",
            f"- Graph used: {row['graph_used']}",
            "",
            "Selected evidence:",
            "",
        ])
        for item in row.get("selected", []):
            lines.append(
                f"- E:{item.get('chunk_id')} | {item.get('title')} | "
                f"{item.get('channels')} | {item.get('document_domains')} | "
                f"role={item.get('evidence_role')} | score={item.get('selector_score')}"
            )
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


async def main() -> None:
    rows = []
    for question_id, kind, question in QUESTIONS:
        print(f"Running {question_id}: {question}", flush=True)
        row = await run_one(question_id, kind, question)
        row["status"] = status_for(row)
        print(
            {
                "id": row["id"],
                "status": row["status"],
                "latency_ms": row["latency_ms"],
                "domains": row["selected_domains"],
                "selected": row["selected_count"],
            },
            flush=True,
        )
        rows.append(row)
    output = write_report(rows)
    print(f"REPORT={output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
