from __future__ import annotations

import asyncio
import argparse
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
from app.services.qa import build_answer_prompt, conversational_reply, ensure_evidence_citations
from app.services.retrieval import hybrid_search


QUESTION_FILE = Path("/tmp/EVAL_QUESTION_REVIEW_V1.md")
REPORT_DIR = Path("/app/knowledge/evaluations")


def _load_questions() -> list[dict]:
    text = QUESTION_FILE.read_text(encoding="utf-8")
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("| Q"):
            continue
        cols = [col.strip() for col in line.strip().strip("|").split("|")]
        if len(cols) < 4:
            continue
        rows.append({
            "id": cols[0],
            "type": cols[1],
            "question": cols[2],
            "expected": cols[3],
        })
    return rows


def _chinese_ratio(value: str) -> float:
    letters = re.findall(r"[A-Za-z]+", value)
    chinese = re.findall(r"[\u4e00-\u9fff]", value)
    total = len(chinese) + sum(len(item) for item in letters)
    return 0.0 if total == 0 else len(chinese) / total


def _preview_flags(row: dict) -> list[str]:
    flags: list[str] = []
    answer = row["answer"]
    if row["mode"] == "rag":
        if row["citation_count"] == 0:
            flags.append("missing_citations")
        if row["chinese_ratio"] < 0.25 and re.search(r"[\u4e00-\u9fff]", row["question"]):
            flags.append("mostly_not_chinese")
        if row["total_ms"] > 20000:
            flags.append("slow_over_20s")
        if len(answer) > 1200:
            flags.append("too_long")
    if row["type"] == "boundary" and row["mode"] != "refusal":
        flags.append("boundary_not_refused")
    if row["mode"] == "refusal" and row["type"] != "boundary":
        flags.append("unexpected_refusal")
    return flags


async def run_one(item: dict, *, profile: str) -> dict:
    settings = get_settings()
    started = perf_counter()
    question = item["question"]

    direct = conversational_reply(question)
    if direct:
        answer = direct["answer"]
        row = {
            **item,
            "mode": "direct",
            "retrieval_ms": 0,
            "generation_ms": 0,
            "total_ms": round((perf_counter() - started) * 1000),
            "coverage": None,
            "citation_ids": [],
            "citation_count": 0,
            "chinese_ratio": round(_chinese_ratio(answer), 3),
            "trace": {"intent": direct.get("intent")},
            "answer": answer,
        }
        row["flags"] = _preview_flags(row)
        return row

    trace: dict = {}
    candidates = await hybrid_search(
        question,
        limit=settings.evidence_candidate_k,
        use_rewrite=False,
        use_rerank=True,
        retrieval_profile=profile,
        trace=trace,
    )
    evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
    retrieval_ms = round((perf_counter() - started) * 1000)
    coverage = assess_evidence_coverage(question, evidence)

    if not coverage.passed:
        answer = insufficient_coverage_answer(coverage)
        row = {
            **item,
            "mode": "refusal",
            "retrieval_ms": retrieval_ms,
            "generation_ms": 0,
            "total_ms": round((perf_counter() - started) * 1000),
            "coverage": {
                "passed": coverage.passed,
                "required_terms": coverage.required_terms,
                "covered_terms": coverage.covered_terms,
                "missing_terms": coverage.missing_terms,
                "reason": coverage.reason,
            },
            "citation_ids": [],
            "citation_count": 0,
            "chinese_ratio": round(_chinese_ratio(answer), 3),
            "trace": {
                "dense_skipped": trace.get("dense_skipped"),
                "dense_skip_reason": trace.get("dense_skip_reason"),
                "retrieval_profile": trace.get("retrieval_profile"),
                "rerank_reason": trace.get("rerank_reason"),
            },
            "answer": answer,
        }
        row["flags"] = _preview_flags(row)
        return row

    prompt, context = build_answer_prompt(question, evidence, [], [], trace=trace)
    generation_started = perf_counter()
    answer = await OllamaClient().generate(prompt, num_predict=answer_generation_budget(question))
    generation_ms = round((perf_counter() - generation_started) * 1000)
    answer, citations = ensure_evidence_citations(answer, evidence)
    row = {
        **item,
        "mode": "rag",
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
        "citation_ids": [citation.get("chunk_id") for citation in citations],
        "citation_count": len(citations),
        "chinese_ratio": round(_chinese_ratio(answer), 3),
        "context_chars": len(context),
        "trace": {
            "dense_skipped": trace.get("dense_skipped"),
            "dense_skip_reason": trace.get("dense_skip_reason"),
            "retrieval_profile": trace.get("retrieval_profile"),
            "rerank_reason": trace.get("rerank_reason"),
            "prompt_compaction": trace.get("prompt_compaction"),
        },
        "answer": answer,
    }
    row["flags"] = _preview_flags(row)
    return row


def _write_report(rows: list[dict], *, profile: str) -> Path:
    report_file = REPORT_DIR / f"EVAL_PREVIEW_V1_{profile.upper()}.md"
    lines = [
        "# Evaluation Preview v1",
        "",
        f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Retrieval profile: `{profile}`",
        f"- Questions: {len(rows)}",
        "- Purpose: preview current answer behavior before manual annotation.",
        "",
        "| ID | Type | Mode | Total ms | Citations | Flags | Question |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['type']} | {row['mode']} | {row['total_ms']} | "
            f"{row['citation_count']} | {', '.join(row['flags']) or '-'} | {row['question']} |"
        )
    lines.extend(["", "## Details", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']} - {row['question']}",
            "",
            f"- Type: {row['type']}",
            f"- Expected behavior: {row['expected']}",
            f"- Mode: {row['mode']}",
            f"- Latency: retrieval={row['retrieval_ms']} ms, generation={row['generation_ms']} ms, total={row['total_ms']} ms",
            f"- Citations: {row['citation_ids']}",
            f"- Chinese ratio: {row['chinese_ratio']}",
            f"- Flags: {row['flags'] or '-'}",
            f"- Coverage: `{json.dumps(row.get('coverage'), ensure_ascii=False)}`",
            f"- Trace: `{json.dumps(row.get('trace'), ensure_ascii=False)}`",
            "",
            "Answer:",
            "",
            row["answer"].strip(),
            "",
        ])
    report_file.write_text("\n".join(lines), encoding="utf-8")
    return report_file


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    args = parser.parse_args()
    rows: list[dict] = []
    for item in _load_questions():
        print(f"RUN {item['id']} {item['question']}", flush=True)
        row = await run_one(item, profile=args.profile)
        print(json.dumps({
            "id": row["id"],
            "mode": row["mode"],
            "total_ms": row["total_ms"],
            "citations": row["citation_count"],
            "flags": row["flags"],
        }, ensure_ascii=False), flush=True)
        rows.append(row)
    report_file = _write_report(rows, profile=args.profile)
    print(f"REPORT={report_file}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
