from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from app.services.chunker import estimate_tokens
from app.services.retrieval_pipeline import retrieve_evidence_with_coverage


DEFAULT_SET = Path("/app/docs/evaluation/EVAL_REGRESSION_V2.json")
DEFAULT_DATABASE = Path("/app/data/app.db")
DEFAULT_OUTPUT = Path("/app/knowledge/evaluations/PIPELINE_DIAGNOSIS_V2.md")


def heading_parts(value: str | None) -> list[str]:
    return [part.strip().casefold() for part in (value or "").split("/") if part.strip()]


def same_heading_family(left: str | None, right: str | None) -> bool:
    left_parts = heading_parts(left)
    right_parts = heading_parts(right)
    if not left_parts or not right_parts:
        return False
    common = 0
    for left_part, right_part in zip(left_parts, right_parts, strict=False):
        if left_part != right_part:
            break
        common += 1
    return common >= 2


def structurally_related(candidate: dict, gold_rows: list[dict], window: int = 2) -> bool:
    for gold in gold_rows:
        if candidate["id"] == gold["id"]:
            return True
        if candidate["document_id"] != gold["document_id"]:
            continue
        if abs(candidate["ordinal"] - gold["ordinal"]) <= window:
            return True
        if same_heading_family(candidate["heading_path"], gold["heading_path"]):
            return True
    return False


def classify(gold: set[int], candidates: list[int], selected: list[int], structural_selected: bool) -> str:
    gold_ranks = [index + 1 for index, item in enumerate(candidates) if item in gold]
    if not gold_ranks:
        return "A2_structural_near_miss" if structural_selected else "A_recall_failure"
    if gold & set(selected):
        return "D_retrieval_ready"
    if min(gold_ranks) > 10:
        return "B_fusion_rank_failure"
    return "C_selector_failure"


async def diagnose_case(case: dict, connection: sqlite3.Connection, profile: str) -> dict:
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
    candidates = result.candidates
    selected = result.evidence
    candidate_ids = [int(item["chunk_id"]) for item in candidates]
    selected_ids = [int(item["chunk_id"]) for item in selected]
    gold = {int(item) for item in case["gold_evidence"]}

    all_ids = sorted(gold | set(candidate_ids) | set(selected_ids))
    placeholders = ",".join("?" for _ in all_ids)
    metadata = {
        int(row["id"]): dict(row)
        for row in connection.execute(
            f"""
            SELECT id, document_id, ordinal, page, heading_path, content
            FROM evidence_chunks WHERE id IN ({placeholders})
            """,
            all_ids,
        ).fetchall()
    }
    gold_rows = [metadata[item] for item in gold if item in metadata]
    structural_candidate_ids = [
        item for item in candidate_ids
        if item in metadata and structurally_related(metadata[item], gold_rows)
    ]
    structural_selected_ids = [item for item in selected_ids if item in structural_candidate_ids]
    category = classify(gold, candidate_ids, selected_ids, bool(structural_selected_ids))

    gold_ranks = {item: candidate_ids.index(item) + 1 for item in gold if item in candidate_ids}
    candidate_by_id = {int(item["chunk_id"]): item for item in candidates}
    gold_channels = {
        item: candidate_by_id[item].get("channels", [])
        for item in gold if item in candidate_by_id
    }
    channel_counts = Counter(
        channel for item in candidates for channel in item.get("channels", [])
    )

    selector = trace.get("evidence_selector") or {}
    rejected = {
        int(item["chunk_id"]): item.get("reason")
        for item in selector.get("rejected", []) if item.get("chunk_id") is not None
    }
    gold_rejection_reasons = {item: rejected.get(item, "not_in_selector_trace") for item in gold if item not in selected_ids}

    gold_sizes = [estimate_tokens(row["content"] or "") for row in gold_rows]
    parent_sizes: list[int] = []
    for gold_row in gold_rows:
        neighbors = connection.execute(
            """
            SELECT heading_path, content FROM evidence_chunks
            WHERE document_id = ? AND ordinal BETWEEN ? AND ?
            ORDER BY ordinal
            """,
            (gold_row["document_id"], gold_row["ordinal"] - 2, gold_row["ordinal"] + 2),
        ).fetchall()
        family = [
            row["content"] or "" for row in neighbors
            if same_heading_family(row["heading_path"], gold_row["heading_path"])
            or row["heading_path"] == gold_row["heading_path"]
        ]
        parent_sizes.append(estimate_tokens(" ".join(family)))

    return {
        "id": case["id"],
        "domain": case["domain"],
        "question": case["question"],
        "category": category,
        "gold_ids": sorted(gold),
        "gold_ranks": gold_ranks,
        "candidate_ids": candidate_ids,
        "selected_ids": selected_ids,
        "structural_candidate_ids": structural_candidate_ids,
        "structural_selected_ids": structural_selected_ids,
        "gold_channels": gold_channels,
        "channel_counts": dict(sorted(channel_counts.items())),
        "gold_rejection_reasons": gold_rejection_reasons,
        "gold_chunk_tokens": gold_sizes,
        "gold_chunk_token_median": round(median(gold_sizes), 1) if gold_sizes else 0,
        "parent_window_tokens": parent_sizes,
        "parent_window_token_median": round(median(parent_sizes), 1) if parent_sizes else 0,
        "fragmentation_risk": bool(gold_sizes and median(gold_sizes) < 250 and parent_sizes and median(parent_sizes) >= 500),
        "question_role": selector.get("question_role"),
        "query_domains": selector.get("query_domains", []),
        "selected_domains": selector.get("selected_domains", []),
        "query_plan": trace.get("query_plan", []),
        "dense_skipped": trace.get("dense_skipped"),
        "dense_skip_reason": trace.get("dense_skip_reason"),
        "rerank_used": trace.get("rerank_used"),
        "rerank_reason": trace.get("rerank_reason"),
        "coverage_passed": result.coverage.passed,
        "coverage_reason": result.coverage.reason,
        "coverage_missing_terms": result.coverage.missing_terms,
        "elapsed_ms": elapsed_ms,
    }


def render(rows: list[dict], database_stats: dict, profile: str) -> str:
    categories = Counter(row["category"] for row in rows)
    fragmentation = sum(row["fragmentation_risk"] for row in rows)
    lines = [
        "# Pipeline Diagnosis v2",
        "",
        f"- Retrieval profile: `{profile}`",
        "- Dataset: v2 development set only; v3 holdout was not used.",
        "- No Top-K, chunking, selector, prompt, or threshold values were changed during this run.",
        "",
        "## Classification",
        "",
        "- `A_recall_failure`: no exact Gold entered the final candidate list.",
        "- `A2_structural_near_miss`: exact Gold missed, but a same-section/neighbor chunk was selected.",
        "- `B_fusion_rank_failure`: Gold entered candidates only below rank 10 and was not selected.",
        "- `C_selector_failure`: Gold reached Top-10 but selector removed it.",
        "- `D_retrieval_ready`: at least one exact Gold reached final evidence; answer generation must be audited separately.",
        "",
        "## Summary",
        "",
        "| Signal | Value |",
        "|---|---:|",
        f"| Cases | {len(rows)} |",
        f"| A exact recall failures | {categories['A_recall_failure']} |",
        f"| A2 structural near misses | {categories['A2_structural_near_miss']} |",
        f"| B fusion rank failures | {categories['B_fusion_rank_failure']} |",
        f"| C selector failures | {categories['C_selector_failure']} |",
        f"| D retrieval ready | {categories['D_retrieval_ready']} |",
        f"| Cases with fragmentation risk | {fragmentation} |",
        f"| Coverage Gate pass rate | {mean(float(row['coverage_passed']) for row in rows):.3f} |",
        f"| Median latency | {round(median(row['elapsed_ms'] for row in rows))} ms |",
        f"| Corpus chunk median | {database_stats['median']} tokens |",
        f"| Corpus chunks under 200 | {database_stats['under_200']} / {database_stats['count']} |",
        "",
        "## Per-question",
        "",
        "| ID | Domain | Class | Best Gold rank | Selected Gold | Structural selected | Gold tokens | Parent window | Fragmented | Gate | ms |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        best_rank = min(row["gold_ranks"].values()) if row["gold_ranks"] else "-"
        selected_gold = int(bool(set(row["gold_ids"]) & set(row["selected_ids"])))
        lines.append(
            f"| {row['id']} | {row['domain']} | {row['category']} | {best_rank} | {selected_gold} | "
            f"{int(bool(row['structural_selected_ids']))} | {row['gold_chunk_token_median']} | "
            f"{row['parent_window_token_median']} | {int(row['fragmentation_risk'])} | "
            f"{int(row['coverage_passed'])} | {row['elapsed_ms']} |"
        )

    lines.extend(["", "## Per-question evidence trace", ""])
    for row in rows:
        lines.extend([
            f"### {row['id']} — {row['category']}",
            "",
            f"- Question: {row['question']}",
            f"- Role / domains: `{row['question_role']}` / `{row['query_domains']}`",
            f"- Gold ranks: `{row['gold_ranks'] or '-'}`",
            f"- Gold channels: `{row['gold_channels'] or '-'}`",
            f"- Selected: `{row['selected_ids']}`",
            f"- Structural selected: `{row['structural_selected_ids'] or '-'}`",
            f"- Gold rejection reasons: `{row['gold_rejection_reasons'] or '-'}`",
            f"- Channel counts: `{row['channel_counts']}`",
            f"- Gold tokens / parent-window tokens: `{row['gold_chunk_tokens']}` / `{row['parent_window_tokens']}`",
            f"- Dense: skipped={row['dense_skipped']}, reason=`{row['dense_skip_reason']}`",
            f"- Rerank: used={row['rerank_used']}, reason=`{row['rerank_reason']}`",
            f"- Coverage: passed={row['coverage_passed']}, reason=`{row['coverage_reason']}`, missing=`{row['coverage_missing_terms']}`",
            "",
        ])
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose v2 retrieval stages without changing runtime settings.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    args = parser.parse_args()

    payload = json.loads(args.set.read_text(encoding="utf-8"))
    assert payload.get("status") == "frozen_v2_baseline", "diagnosis must use the v2 development set"
    cases = [case for case in payload["cases"] if case["expected_mode"] == "rag"]
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    all_contents = [row[0] or "" for row in connection.execute("SELECT content FROM evidence_chunks")]
    sizes = [estimate_tokens(value) for value in all_contents]
    database_stats = {
        "count": len(sizes),
        "median": round(median(sizes), 1),
        "under_200": sum(value < 200 for value in sizes),
    }

    rows: list[dict] = []
    for case in cases:
        print(f"RUN {case['id']}", flush=True)
        row = await diagnose_case(case, connection, args.profile)
        rows.append(row)
        print(json.dumps({key: row[key] for key in ("id", "category", "gold_ranks", "fragmentation_risk", "elapsed_ms")}, ensure_ascii=False), flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(rows, database_stats, args.profile), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps({"database_stats": database_stats, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={args.output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
