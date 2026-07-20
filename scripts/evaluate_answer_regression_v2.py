from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter

from app.services.coverage import insufficient_coverage_answer
from app.services.evidence_snippets import answer_generation_budget
from app.services.ollama import OllamaClient
from app.services.qa import build_answer_prompt, conversational_reply, ensure_evidence_citations
from app.services.retrieval_pipeline import retrieve_evidence_with_coverage


DEFAULT_SET = Path("/app/docs/evaluation/EVAL_REGRESSION_V2.json")
DEFAULT_OUTPUT = Path("/app/knowledge/evaluations/ANSWER_REGRESSION_V2_SAMPLE.md")


def looks_like_refusal(answer: str) -> bool:
    normalized = re.sub(r"\s+", "", answer).casefold()
    cues = (
        "当前知识库中没有", "没有足够证据", "无法确定", "无法判断", "不能判断",
        "未提供", "需要提供", "无法给出", "不能给出", "资料不足",
    )
    return any(cue in normalized for cue in cues)


def flags_for(row: dict) -> list[str]:
    flags: list[str] = []
    if row["actual_mode"] != row["expected_mode"]:
        flags.append("mode_mismatch")
    if row["expected_mode"] == "rag" and not row["citation_ids"]:
        flags.append("missing_citations")
    if row["expected_mode"] == "rag" and len(row["answer"]) > 1400:
        flags.append("too_long")
    if row["total_ms"] > 30000:
        flags.append("slow_over_30s")
    return flags


async def evaluate_case(case: dict, profile: str) -> dict:
    started = perf_counter()
    direct = conversational_reply(case["question"])
    if direct:
        answer = direct["answer"]
        row = {
            "id": case["id"], "question": case["question"], "expected_mode": case["expected_mode"],
            "actual_mode": direct.get("mode", "direct"), "answer": answer, "citation_ids": [],
            "selected_ids": [], "retrieval_ms": 0, "generation_ms": 0,
            "total_ms": round((perf_counter() - started) * 1000), "coverage": None,
            "must_cover": case["must_cover"], "forbidden": case["forbidden"],
        }
        row["flags"] = flags_for(row)
        return row

    trace: dict = {}
    retrieval_started = perf_counter()
    retrieval = await retrieve_evidence_with_coverage(
        case["question"], use_rewrite=False, use_rerank=True,
        retrieval_profile=profile, trace=trace,
    )
    retrieval_ms = round((perf_counter() - retrieval_started) * 1000)
    selected_ids = [int(item["chunk_id"]) for item in retrieval.evidence]
    if not retrieval.coverage.passed:
        answer = insufficient_coverage_answer(retrieval.coverage)
        actual_mode = "refusal"
        generation_ms = 0
        citation_ids: list[int] = []
    else:
        prompt, _context = build_answer_prompt(case["question"], retrieval.evidence, [], [], trace=trace)
        generation_started = perf_counter()
        answer = await OllamaClient().generate(
            prompt,
            num_predict=answer_generation_budget(case["question"]),
        )
        generation_ms = round((perf_counter() - generation_started) * 1000)
        answer, citations = ensure_evidence_citations(answer, retrieval.evidence)
        citation_ids = [int(item["chunk_id"]) for item in citations]
        actual_mode = "refusal" if looks_like_refusal(answer) else "rag"

    row = {
        "id": case["id"], "question": case["question"], "expected_mode": case["expected_mode"],
        "actual_mode": actual_mode, "answer": answer, "citation_ids": citation_ids,
        "selected_ids": selected_ids, "retrieval_ms": retrieval_ms, "generation_ms": generation_ms,
        "total_ms": round((perf_counter() - started) * 1000),
        "coverage": {
            "passed": retrieval.coverage.passed,
            "reason": retrieval.coverage.reason,
            "missing_terms": retrieval.coverage.missing_terms,
        },
        "must_cover": case["must_cover"], "forbidden": case["forbidden"],
    }
    row["flags"] = flags_for(row)
    return row


def render(rows: list[dict], profile: str) -> str:
    lines = [
        "# Answer Regression v2 - Human Review",
        "",
        f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Retrieval profile: `{profile}`",
        f"- Cases: {len(rows)}",
        "- Local Judge intentionally not used as ground truth; review must-cover, forbidden claims, citations, and usefulness manually.",
        "",
        "| ID | Expected | Actual | Retrieval ms | Generation ms | Total ms | Citations | Flags |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['expected_mode']} | {row['actual_mode']} | {row['retrieval_ms']} | "
            f"{row['generation_ms']} | {row['total_ms']} | {row['citation_ids'] or '-'} | "
            f"{', '.join(row['flags']) or '-'} |"
        )
    lines.extend(["", "## Details", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']} — {row['question']}",
            "",
            f"- Expected / actual: `{row['expected_mode']}` / `{row['actual_mode']}`",
            f"- Selected evidence: {row['selected_ids']}",
            f"- Citations: {row['citation_ids']}",
            f"- Must cover: {json.dumps(row['must_cover'], ensure_ascii=False)}",
            f"- Forbidden: {json.dumps(row['forbidden'], ensure_ascii=False)}",
            f"- Coverage: `{json.dumps(row['coverage'], ensure_ascii=False)}`",
            f"- Flags: {row['flags'] or '-'}",
            "",
            row["answer"].strip(),
            "",
        ])
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate answers from frozen v2 cases for human review.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    payload = json.loads(args.set.read_text(encoding="utf-8"))
    assert payload.get("status") == "frozen_v2_baseline"
    selected = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    cases = payload["cases"]
    if selected:
        cases = [case for case in cases if case["id"].upper() in selected]

    rows: list[dict] = []
    for case in cases:
        print(f"RUN {case['id']}", flush=True)
        row = await evaluate_case(case, args.profile)
        rows.append(row)
        print(json.dumps({key: row[key] for key in ("id", "actual_mode", "total_ms", "citation_ids", "flags")}, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(rows, args.profile), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={args.output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
