from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.services.coverage import assess_evidence_coverage
from app.services.evidence_selector import select_evidence
from app.services.retrieval import hybrid_search


DEFAULT_GOLD = Path("/app/docs/evaluation/EVAL_LABELS_V1_GOLD_DRAFT.md")
DEFAULT_REPORT = Path("/app/knowledge/evaluations/EVAL_PREVIEW_V1_FAST.md")
DEFAULT_OUTPUT = Path("/app/knowledge/evaluations/GOLD_METRICS_FAST.md")


@dataclass
class GoldRow:
    question_id: str
    qtype: str
    question: str
    expected_mode: str
    decision: str
    expected_evidence: list[int]
    must_cover: str
    forbidden: str
    notes: str


@dataclass
class ReportRow:
    question_id: str
    mode: str
    citations: list[int]
    flags: list[str]
    total_ms: int | None


def _parse_ints(value: str) -> list[int]:
    return [int(item) for item in re.findall(r"\d+", value or "")]


def _parse_gold(path: Path) -> dict[str, GoldRow]:
    rows: dict[str, GoldRow] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\| Q\d{3} \|", line):
            continue
        cols = [col.strip() for col in line.strip().strip("|").split("|")]
        if len(cols) < 12:
            continue
        rows[cols[0]] = GoldRow(
            question_id=cols[0],
            qtype=cols[1],
            question=cols[2],
            expected_mode=cols[3],
            decision=cols[4],
            expected_evidence=_parse_ints(cols[5]),
            must_cover=cols[6],
            forbidden=cols[7],
            notes=cols[11],
        )
    return rows


def _parse_flags(value: str) -> list[str]:
    value = value.strip()
    if not value or value == "-":
        return []
    return [item.strip().strip("'\"") for item in value.strip("[]").split(",") if item.strip()]


def _parse_report(path: Path) -> dict[str, ReportRow]:
    text = path.read_text(encoding="utf-8")
    rows: dict[str, ReportRow] = {}
    for match in re.finditer(r"^### (Q\d{3}) - .+?(?=^### Q\d{3} - |\Z)", text, re.M | re.S):
        section = match.group(0)
        question_id = match.group(1)
        mode = re.search(r"^- Mode: (.+)$", section, re.M)
        citations = re.search(r"^- Citations: \[(.*?)\]$", section, re.M)
        flags = re.search(r"^- Flags: (.+)$", section, re.M)
        latency = re.search(r"total=(\d+) ms", section)
        rows[question_id] = ReportRow(
            question_id=question_id,
            mode=mode.group(1).strip() if mode else "",
            citations=_parse_ints(citations.group(1) if citations else ""),
            flags=_parse_flags(flags.group(1) if flags else ""),
            total_ms=int(latency.group(1)) if latency else None,
        )
    return rows


def _precision(predicted: list[int], gold: list[int]) -> float | None:
    if not predicted:
        return None
    if not gold:
        return None
    gold_set = set(gold)
    return len([item for item in predicted if item in gold_set]) / len(predicted)


def _recall(predicted: list[int], gold: list[int]) -> float | None:
    if not gold:
        return None
    predicted_set = set(predicted)
    return len([item for item in gold if item in predicted_set]) / len(gold)


def _mrr(ids: list[int], gold: list[int]) -> float | None:
    if not gold:
        return None
    gold_set = set(gold)
    for index, chunk_id in enumerate(ids, 1):
        if chunk_id in gold_set:
            return 1 / index
    return 0.0


def _any_hit(ids: list[int], gold: list[int]) -> float | None:
    if not gold:
        return None
    return 1.0 if set(ids) & set(gold) else 0.0


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{value:.3f}"


async def _evaluate_retrieval(question: str, gold_ids: list[int]) -> dict:
    if not gold_ids:
        return {
            "candidate_ids": [],
            "selected_ids": [],
            "candidate_recall_10": None,
            "candidate_recall_20": None,
            "candidate_recall_all": None,
            "candidate_any_hit_10": None,
            "candidate_any_hit_20": None,
            "candidate_any_hit_all": None,
            "candidate_mrr": None,
            "selected_any_hit": None,
            "selected_recall": None,
            "coverage_passed": None,
        }
    settings = get_settings()
    trace: dict = {}
    candidates = await hybrid_search(
        question,
        limit=max(settings.evidence_candidate_k, 30),
        use_rewrite=False,
        use_rerank=True,
        retrieval_profile="fast",
        trace=trace,
    )
    selected = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=trace)
    candidate_ids = [int(item["chunk_id"]) for item in candidates]
    selected_ids = [int(item["chunk_id"]) for item in selected]
    coverage = assess_evidence_coverage(question, selected)
    return {
        "candidate_ids": candidate_ids,
        "selected_ids": selected_ids,
        "candidate_recall_10": _recall(candidate_ids[:10], gold_ids),
        "candidate_recall_20": _recall(candidate_ids[:20], gold_ids),
        "candidate_recall_all": _recall(candidate_ids, gold_ids),
        "candidate_any_hit_10": _any_hit(candidate_ids[:10], gold_ids),
        "candidate_any_hit_20": _any_hit(candidate_ids[:20], gold_ids),
        "candidate_any_hit_all": _any_hit(candidate_ids, gold_ids),
        "candidate_mrr": _mrr(candidate_ids, gold_ids),
        "selected_recall": _recall(selected_ids, gold_ids),
        "selected_any_hit": _any_hit(selected_ids, gold_ids),
        "coverage_passed": coverage.passed,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    gold_rows = _parse_gold(args.gold)
    report_rows = _parse_report(args.report)
    evaluated: list[dict] = []
    for question_id, gold in gold_rows.items():
        report = report_rows.get(question_id, ReportRow(question_id, "", [], [], None))
        retrieval = await _evaluate_retrieval(gold.question, gold.expected_evidence)
        citation_precision = _precision(report.citations, gold.expected_evidence)
        citation_recall = _recall(report.citations, gold.expected_evidence)
        citation_any_hit = _any_hit(report.citations, gold.expected_evidence)
        mode_ok = report.mode == gold.expected_mode
        evaluated.append({
            "id": question_id,
            "type": gold.qtype,
            "question": gold.question,
            "expected_mode": gold.expected_mode,
            "actual_mode": report.mode,
            "mode_ok": mode_ok,
            "gold": gold.expected_evidence,
            "citations": report.citations,
            "citation_precision": citation_precision,
            "citation_recall": citation_recall,
            "citation_any_hit": citation_any_hit,
            "candidate_recall_10": retrieval["candidate_recall_10"],
            "candidate_recall_20": retrieval["candidate_recall_20"],
            "candidate_recall_all": retrieval["candidate_recall_all"],
            "candidate_any_hit_10": retrieval["candidate_any_hit_10"],
            "candidate_any_hit_20": retrieval["candidate_any_hit_20"],
            "candidate_any_hit_all": retrieval["candidate_any_hit_all"],
            "candidate_mrr": retrieval["candidate_mrr"],
            "selected_ids": retrieval["selected_ids"],
            "selected_recall": retrieval["selected_recall"],
            "selected_any_hit": retrieval["selected_any_hit"],
            "coverage_passed": retrieval["coverage_passed"],
            "flags": report.flags,
            "total_ms": report.total_ms,
        })

    answerable = [row for row in evaluated if row["gold"]]
    mode_accuracy = sum(1 for row in evaluated if row["mode_ok"]) / max(1, len(evaluated))
    citation_precision_values = [row["citation_precision"] for row in answerable if row["citation_precision"] is not None]
    citation_recall_values = [row["citation_recall"] for row in answerable if row["citation_recall"] is not None]
    citation_any_values = [row["citation_any_hit"] for row in answerable if row["citation_any_hit"] is not None]
    recall10_values = [row["candidate_recall_10"] for row in answerable if row["candidate_recall_10"] is not None]
    recall20_values = [row["candidate_recall_20"] for row in answerable if row["candidate_recall_20"] is not None]
    recall_all_values = [row["candidate_recall_all"] for row in answerable if row["candidate_recall_all"] is not None]
    any10_values = [row["candidate_any_hit_10"] for row in answerable if row["candidate_any_hit_10"] is not None]
    any20_values = [row["candidate_any_hit_20"] for row in answerable if row["candidate_any_hit_20"] is not None]
    any_all_values = [row["candidate_any_hit_all"] for row in answerable if row["candidate_any_hit_all"] is not None]
    mrr_values = [row["candidate_mrr"] for row in answerable if row["candidate_mrr"] is not None]
    selected_recall_values = [row["selected_recall"] for row in answerable if row["selected_recall"] is not None]
    selected_any_values = [row["selected_any_hit"] for row in answerable if row["selected_any_hit"] is not None]

    def avg(values: list[float]) -> float | None:
        return None if not values else sum(values) / len(values)

    lines = [
        "# Gold Metrics - Fast Profile",
        "",
        f"- Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Gold labels: `{args.gold}`",
        f"- Answer report: `{args.report}`",
        "- Method: compare current fast report and current fast retrieval against source-document gold labels.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Mode Accuracy | {_fmt(mode_accuracy)} |",
        f"| Citation Precision avg | {_fmt(avg(citation_precision_values))} |",
        f"| Citation Recall avg | {_fmt(avg(citation_recall_values))} |",
        f"| Citation Any Gold Hit | {_fmt(avg(citation_any_values))} |",
        f"| Candidate Recall@10 avg | {_fmt(avg(recall10_values))} |",
        f"| Candidate Recall@20 avg | {_fmt(avg(recall20_values))} |",
        f"| Candidate Recall@All avg | {_fmt(avg(recall_all_values))} |",
        f"| Candidate Any Gold Hit@10 | {_fmt(avg(any10_values))} |",
        f"| Candidate Any Gold Hit@20 | {_fmt(avg(any20_values))} |",
        f"| Candidate Any Gold Hit@All | {_fmt(avg(any_all_values))} |",
        f"| Candidate MRR avg | {_fmt(avg(mrr_values))} |",
        f"| Selected Evidence Recall avg | {_fmt(avg(selected_recall_values))} |",
        f"| Selected Any Gold Hit | {_fmt(avg(selected_any_values))} |",
        "",
        "## Per-question Metrics",
        "",
        "| ID | Mode | Gold | Cited | Citation P | Citation R | Cite Hit | Hit@10 | Hit@20 | R@All | MRR | Selected Hit | Flags |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in evaluated:
        mode = "ok" if row["mode_ok"] else f"{row['actual_mode']}≠{row['expected_mode']}"
        lines.append(
            f"| {row['id']} | {mode} | {','.join(map(str, row['gold'])) or '-'} | "
            f"{','.join(map(str, row['citations'])) or '-'} | "
            f"{_fmt(row['citation_precision'])} | {_fmt(row['citation_recall'])} | "
            f"{_fmt(row['citation_any_hit'])} | {_fmt(row['candidate_any_hit_10'])} | "
            f"{_fmt(row['candidate_any_hit_20'])} | {_fmt(row['candidate_recall_all'])} | "
            f"{_fmt(row['candidate_mrr'])} | {_fmt(row['selected_any_hit'])} | "
            f"{','.join(row['flags']) or '-'} |"
        )

    lines.extend([
        "",
        "## Needs Attention",
        "",
    ])
    attention = [
        row for row in evaluated
        if not row["mode_ok"]
        or (row["gold"] and (row["candidate_any_hit_all"] or 0) < 1.0)
        or (row["gold"] and row["citation_any_hit"] is not None and row["citation_any_hit"] < 1.0)
        or row["flags"]
    ]
    if not attention:
        lines.append("- None by current thresholds.")
    else:
        for row in attention:
            reasons: list[str] = []
            if not row["mode_ok"]:
                reasons.append("mode_mismatch")
            if row["gold"] and (row["candidate_any_hit_all"] or 0) < 1.0:
                reasons.append("no_gold_in_candidates")
            if row["gold"] and row["citation_any_hit"] is not None and row["citation_any_hit"] < 1.0:
                reasons.append("no_gold_in_citations")
            if row["flags"]:
                reasons.append("flags=" + ",".join(row["flags"]))
            lines.append(f"- {row['id']}: {', '.join(reasons)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"REPORT={args.output}")
    print(json.dumps({
        "mode_accuracy": mode_accuracy,
        "citation_precision_avg": avg(citation_precision_values),
        "citation_recall_avg": avg(citation_recall_values),
        "citation_any_gold_hit": avg(citation_any_values),
        "candidate_recall_10_avg": avg(recall10_values),
        "candidate_recall_20_avg": avg(recall20_values),
        "candidate_recall_all_avg": avg(recall_all_values),
        "candidate_any_gold_hit_10": avg(any10_values),
        "candidate_any_gold_hit_20": avg(any20_values),
        "candidate_any_gold_hit_all": avg(any_all_values),
        "candidate_mrr_avg": avg(mrr_values),
        "selected_recall_avg": avg(selected_recall_values),
        "selected_any_gold_hit": avg(selected_any_values),
        "attention": [row["id"] for row in attention],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
