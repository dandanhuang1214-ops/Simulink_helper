from __future__ import annotations

import argparse
import re
from pathlib import Path


DEFAULT_LABEL_FILE = Path("docs/evaluation/EVAL_LABELS_V1_TEMPLATE.md")


def _parse_score(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\| Q\d{3} \|", line):
            continue
        cols = [col.strip() for col in line.strip().strip("|").split("|")]
        if len(cols) < 12:
            continue
        rows.append({
            "id": cols[0],
            "type": cols[1],
            "question": cols[2],
            "expected_mode": cols[3],
            "decision": cols[4],
            "expected_evidence": cols[5],
            "must_cover": cols[6],
            "forbidden": cols[7],
            "answer_score": _parse_score(cols[8]),
            "faithfulness_score": _parse_score(cols[9]),
            "citation_score": _parse_score(cols[10]),
            "notes": cols[11],
        })
    return rows


def _avg(values: list[int]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABEL_FILE)
    args = parser.parse_args()

    rows = _parse_rows(args.labels)
    annotated = [row for row in rows if row["decision"] or row["answer_score"] is not None]
    decisions: dict[str, int] = {}
    for row in annotated:
        key = row["decision"] or "unlabeled_decision"
        decisions[key] = decisions.get(key, 0) + 1

    answer_scores = [row["answer_score"] for row in annotated if row["answer_score"] is not None]
    faithfulness_scores = [row["faithfulness_score"] for row in annotated if row["faithfulness_score"] is not None]
    citation_scores = [row["citation_score"] for row in annotated if row["citation_score"] is not None]
    needs_review = [
        row for row in annotated
        if row["decision"] in {"partial", "fail", "revise_question", "remove"}
        or (row["answer_score"] is not None and row["answer_score"] < 2)
        or (row["faithfulness_score"] is not None and row["faithfulness_score"] < 2)
        or (row["citation_score"] is not None and row["citation_score"] < 2)
    ]

    print(f"label_file={args.labels}")
    print(f"total_questions={len(rows)}")
    print(f"annotated={len(annotated)}")
    print(f"decisions={decisions}")
    print(f"avg_answer_score={_avg(answer_scores)}")
    print(f"avg_faithfulness_score={_avg(faithfulness_scores)}")
    print(f"avg_citation_score={_avg(citation_scores)}")
    if needs_review:
        print("needs_review=" + ",".join(row["id"] for row in needs_review))
        for row in needs_review:
            print(
                f"- {row['id']} decision={row['decision'] or '-'} "
                f"answer={row['answer_score']} faithfulness={row['faithfulness_score']} "
                f"citation={row['citation_score']} notes={row['notes'] or '-'}"
            )


if __name__ == "__main__":
    main()
