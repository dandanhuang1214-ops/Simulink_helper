from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from statistics import mean


DEFAULT_SET = Path("/app/docs/evaluation/EVAL_REGRESSION_V2.json")
DEFAULT_RESULTS = Path("/app/knowledge/evaluations/RETRIEVAL_REGRESSION_V2_FAST_POSTFIX.json")
DEFAULT_DATABASE = Path("/app/data/app.db")
DEFAULT_OUTPUT = Path("/app/knowledge/evaluations/STRUCTURAL_RELEVANCE_V2_FAST_POSTFIX.md")


def heading_parts(value: str | None) -> list[str]:
    return [part.strip().casefold() for part in (value or "").split("/") if part.strip()]


def same_heading_family(left: str | None, right: str | None) -> bool:
    left_parts = heading_parts(left)
    right_parts = heading_parts(right)
    if not left_parts or not right_parts:
        return False
    common = 0
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part != right_part:
            break
        common += 1
    # Requiring two shared hierarchy levels avoids counting an entire manual
    # as relevant merely because it shares a top-level chapter.
    return common >= 2


def expected_section_match(heading: str | None, expected_sections: list[str]) -> bool:
    heading_tokens = set(re.findall(r"[a-z0-9]+", (heading or "").casefold()))
    ignored = {"a", "an", "and", "as", "for", "in", "into", "model", "models", "of", "the", "to", "using", "with"}
    for section in expected_sections:
        section_tokens = set(re.findall(r"[a-z0-9]+", section.casefold())) - ignored
        if section_tokens and len(section_tokens & heading_tokens) / len(section_tokens) >= 0.6:
            return True
    return False


def structurally_relevant(candidate: dict, gold_rows: list[dict], ordinal_window: int) -> bool:
    for gold in gold_rows:
        if candidate["id"] == gold["id"]:
            return True
        if candidate["document_id"] != gold["document_id"]:
            continue
        if abs(candidate["ordinal"] - gold["ordinal"]) <= ordinal_window:
            return True
        if same_heading_family(candidate["heading_path"], gold["heading_path"]):
            return True
    return False


def any_hit(ids: list[int], relevant: set[int], k: int | None = None) -> float:
    values = ids if k is None else ids[:k]
    return float(bool(set(values) & relevant))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit exact Gold misses using immutable chunk structure.")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ordinal-window", type=int, default=2)
    args = parser.parse_args()

    evaluation = json.loads(args.set.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in evaluation["cases"]}
    rows = json.loads(args.results.read_text(encoding="utf-8"))
    all_ids = sorted({item for row in rows for item in [*row["gold"], *row["candidate_ids"], *row["selected_ids"]]})
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in all_ids)
    metadata = {
        int(row["id"]): dict(row)
        for row in connection.execute(
            f"SELECT id, document_id, ordinal, page, heading_path FROM evidence_chunks WHERE id IN ({placeholders})",
            all_ids,
        ).fetchall()
    }

    audited: list[dict] = []
    for row in rows:
        case = cases[row["id"]]
        gold_rows = [metadata[item] for item in row["gold"]]
        candidate_rows = [metadata[item] for item in row["candidate_ids"] if item in metadata]
        structural_ids = {
            item["id"] for item in candidate_rows
            if structurally_relevant(item, gold_rows, args.ordinal_window)
        }
        section_ids = {
            item["id"] for item in candidate_rows
            if expected_section_match(item["heading_path"], case["expected_sections"])
        }
        candidate_ids = [int(item) for item in row["candidate_ids"]]
        selected_ids = [int(item) for item in row["selected_ids"]]
        audited.append({
            "id": row["id"],
            "exact_hit_30": any_hit(candidate_ids, set(row["gold"]), 30),
            "structural_hit_10": any_hit(candidate_ids, structural_ids, 10),
            "structural_hit_30": any_hit(candidate_ids, structural_ids, 30),
            "structural_selected_hit": any_hit(selected_ids, structural_ids),
            "section_hit_10": any_hit(candidate_ids, section_ids, 10),
            "section_selected_hit": any_hit(selected_ids, section_ids),
            "structural_candidate_ids": [item for item in candidate_ids[:30] if item in structural_ids],
            "section_candidate_ids": [item for item in candidate_ids[:30] if item in section_ids],
        })

    lines = [
        "# Structural Relevance Audit v2",
        "",
        f"- Retrieval results: `{args.results}`",
        f"- Ordinal window: ±{args.ordinal_window} chunks",
        "- Structural relevance: exact Gold, same document within the ordinal window, or two shared heading levels.",
        "- Section signal: deterministic token match against the predeclared expected sections.",
        "- These signals supplement strict Gold metrics; they do not modify the frozen Gold set.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Exact Any-Gold Hit@30 | {mean(row['exact_hit_30'] for row in audited):.3f} |",
        f"| Structural Hit@10 | {mean(row['structural_hit_10'] for row in audited):.3f} |",
        f"| Structural Hit@30 | {mean(row['structural_hit_30'] for row in audited):.3f} |",
        f"| Structural Selected Hit | {mean(row['structural_selected_hit'] for row in audited):.3f} |",
        f"| Expected-Section Hit@10 | {mean(row['section_hit_10'] for row in audited):.3f} |",
        f"| Expected-Section Selected Hit | {mean(row['section_selected_hit'] for row in audited):.3f} |",
        "",
        "## Per-question",
        "",
        "| ID | Exact@30 | Struct@10 | Struct@30 | Struct Selected | Section@10 | Section Selected |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in audited:
        lines.append(
            f"| {row['id']} | {row['exact_hit_30']:.0f} | {row['structural_hit_10']:.0f} | "
            f"{row['structural_hit_30']:.0f} | {row['structural_selected_hit']:.0f} | "
            f"{row['section_hit_10']:.0f} | {row['section_selected_hit']:.0f} |"
        )
    lines.extend(["", "## Evidence IDs", ""])
    for row in audited:
        lines.append(
            f"- {row['id']}: structural={row['structural_candidate_ids'] or '-'}; "
            f"section={row['section_candidate_ids'] or '-'}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(audited, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
