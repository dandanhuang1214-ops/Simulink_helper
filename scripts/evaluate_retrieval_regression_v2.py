from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from app.services.retrieval_pipeline import retrieve_evidence_with_coverage
from app.services.evaluation_refs import resolve_gold_evidence


DEFAULT_SET = Path("/app/docs/evaluation/EVAL_REGRESSION_V2.json")
DEFAULT_OUTPUT = Path("/app/knowledge/evaluations/RETRIEVAL_REGRESSION_V2_FAST.md")


def recall(ids: list[int], gold: list[int], k: int | None = None) -> float:
    ranked = ids if k is None else ids[:k]
    return len(set(ranked) & set(gold)) / len(set(gold))


def hit(ids: list[int], gold: list[int], k: int | None = None) -> float:
    ranked = ids if k is None else ids[:k]
    return float(bool(set(ranked) & set(gold)))


def reciprocal_rank(ids: list[int], gold: list[int], k: int = 30) -> float:
    gold_set = set(gold)
    for rank, evidence_id in enumerate(ids[:k], 1):
        if evidence_id in gold_set:
            return 1.0 / rank
    return 0.0


def ndcg(ids: list[int], gold: list[int], k: int = 10) -> float:
    gold_set = set(gold)
    dcg = sum(1.0 / math.log2(rank + 1) for rank, item in enumerate(ids[:k], 1) if item in gold_set)
    ideal_count = min(k, len(gold_set))
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


async def evaluate_case(case: dict, profile: str) -> dict:
    started = perf_counter()
    trace: dict = {}
    result = await retrieve_evidence_with_coverage(
        case["question"],
        use_rewrite=False,
        use_rerank=True,
        retrieval_profile=profile,
        trace=trace,
    )
    elapsed_ms = round((perf_counter() - started) * 1000)
    candidate_ids = [int(item["chunk_id"]) for item in result.candidates]
    selected_ids = [int(item["chunk_id"]) for item in result.evidence]
    gold = resolve_gold_evidence(case)
    hit10 = hit(candidate_ids, gold, 10)
    hit30 = hit(candidate_ids, gold, 30)
    selected_hit = hit(selected_ids, gold)
    if hit10 and selected_hit:
        outcome = "pass"
    elif hit30:
        outcome = "partial"
    else:
        outcome = "miss"
    return {
        "id": case["id"],
        "domain": case["domain"],
        "type": case["type"],
        "question": case["question"],
        "gold": gold,
        "candidate_ids": candidate_ids,
        "selected_ids": selected_ids,
        "hit_5": hit(candidate_ids, gold, 5),
        "hit_10": hit10,
        "hit_20": hit(candidate_ids, gold, 20),
        "hit_30": hit30,
        "recall_10": recall(candidate_ids, gold, 10),
        "recall_20": recall(candidate_ids, gold, 20),
        "recall_30": recall(candidate_ids, gold, 30),
        "mrr_30": reciprocal_rank(candidate_ids, gold),
        "ndcg_10": ndcg(candidate_ids, gold),
        "selected_hit": selected_hit,
        "selected_recall": recall(selected_ids, gold),
        "coverage_passed": result.coverage.passed,
        "coverage_reason": result.coverage.reason,
        "elapsed_ms": elapsed_ms,
        "fallback_used": result.fallback_used,
        "dense_skipped": trace.get("dense_skipped"),
        "dense_skip_reason": trace.get("dense_skip_reason"),
        "selected_domains": trace.get("selected_domains", []),
        "outcome": outcome,
    }


def render_report(rows: list[dict], source: Path, profile: str, version: str) -> str:
    def average(key: str) -> float:
        return mean(float(row[key]) for row in rows)

    latencies = [int(row["elapsed_ms"]) for row in rows]
    counts = {name: sum(row["outcome"] == name for row in rows) for name in ("pass", "partial", "miss")}
    lines = [
        f"# Retrieval Regression v{version}",
        "",
        f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Frozen set: `{source}`",
        f"- Retrieval profile: `{profile}`",
        f"- RAG questions: {len(rows)}",
        "- Gold policy: source-first annotations frozen before this run.",
        "- Exact chunk metrics are intentionally strict; Gold is not treated as an exhaustive list of every acceptable neighboring chunk.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Candidate Any-Gold Hit@5 | {average('hit_5'):.3f} |",
        f"| Candidate Any-Gold Hit@10 | {average('hit_10'):.3f} |",
        f"| Candidate Any-Gold Hit@20 | {average('hit_20'):.3f} |",
        f"| Candidate Any-Gold Hit@30 | {average('hit_30'):.3f} |",
        f"| Candidate Recall@10 | {average('recall_10'):.3f} |",
        f"| Candidate Recall@20 | {average('recall_20'):.3f} |",
        f"| Candidate Recall@30 | {average('recall_30'):.3f} |",
        f"| MRR@30 | {average('mrr_30'):.3f} |",
        f"| nDCG@10 | {average('ndcg_10'):.3f} |",
        f"| Selected Any-Gold Hit | {average('selected_hit'):.3f} |",
        f"| Selected Gold Recall | {average('selected_recall'):.3f} |",
        f"| Coverage Gate Pass | {average('coverage_passed'):.3f} |",
        f"| Outcome pass / partial / miss | {counts['pass']} / {counts['partial']} / {counts['miss']} |",
        f"| Retrieval latency median / p95 | {round(median(latencies))} / {percentile(latencies, 0.95)} ms |",
        "",
        "## Per-question",
        "",
        "| ID | Domain | Result | Hit@10 | Hit@30 | R@30 | MRR | Selected | Coverage | ms |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['domain']} | {row['outcome']} | {row['hit_10']:.0f} | "
            f"{row['hit_30']:.0f} | {row['recall_30']:.3f} | {row['mrr_30']:.3f} | "
            f"{row['selected_hit']:.0f} | {int(row['coverage_passed'])} | {row['elapsed_ms']} |"
        )
    lines.extend(["", "## Diagnostics", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']} — {row['outcome']}",
            "",
            f"- Question: {row['question']}",
            f"- Gold: {row['gold']}",
            f"- Candidates: {row['candidate_ids'][:30]}",
            f"- Selected: {row['selected_ids']}",
            f"- Coverage: `{row['coverage_reason']}`",
            f"- Dense: skipped={row['dense_skipped']}, reason={row['dense_skip_reason']}",
            f"- Fallback used: {row['fallback_used']}",
            "",
        ])
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the runtime retrieval pipeline on frozen v2 Gold.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    parser.add_argument("--only", default="", help="Comma-separated case IDs.")
    args = parser.parse_args()

    payload = json.loads(args.set.read_text(encoding="utf-8"))
    assert payload.get("status") in {
        "frozen_v2_baseline", "frozen_v3_holdout", "development_regression_v3_1",
    }, (
        "evaluation set must be frozen before running"
    )
    selected = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    cases = [case for case in payload["cases"] if case["expected_mode"] == "rag"]
    if selected:
        cases = [case for case in cases if case["id"].upper() in selected]

    rows: list[dict] = []
    for case in cases:
        print(f"RUN {case['id']} {case['question']}", flush=True)
        row = await evaluate_case(case, args.profile)
        rows.append(row)
        print(json.dumps({key: row[key] for key in ("id", "outcome", "hit_10", "hit_30", "selected_hit", "elapsed_ms")}, ensure_ascii=False), flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(rows, args.set, args.profile, str(payload.get("version", "unknown"))), encoding="utf-8")
    json_output = args.output.with_suffix(".json")
    json_output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={args.output}", flush=True)
    print(f"JSON={json_output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
