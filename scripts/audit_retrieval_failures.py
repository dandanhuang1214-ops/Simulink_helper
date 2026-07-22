from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export evidence text for failed retrieval cases.")
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-limit", type=int, default=10)
    args = parser.parse_args()

    diagnosis = json.loads(args.diagnosis.read_text(encoding="utf-8"))
    rows = [row for row in diagnosis["rows"] if row["category"] != "D_retrieval_ready"]
    ids = sorted({
        int(chunk_id)
        for row in rows
        for chunk_id in [*row["gold_ids"], *row["candidate_ids"][: args.candidate_limit], *row["selected_ids"]]
    })
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in ids)
    evidence = {
        int(row["id"]): dict(row)
        for row in connection.execute(
            f"""
            SELECT e.id, e.document_id, e.ordinal, e.page, e.heading_path, e.content,
                   d.title AS document_title, d.product, d.release
            FROM evidence_chunks e
            JOIN kb_documents d ON d.id = e.document_id
            WHERE e.id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    }

    def describe(chunk_id: int) -> dict:
        item = evidence.get(int(chunk_id), {"id": int(chunk_id), "missing": True})
        if "content" in item:
            item["content"] = " ".join((item["content"] or "").split())[:500]
        return item

    output = []
    for row in rows:
        output.append({
            "id": row["id"],
            "domain": row["domain"],
            "category": row["category"],
            "question": row["question"],
            "gold": [describe(item) for item in row["gold_ids"]],
            "top_candidates": [describe(item) for item in row["candidate_ids"][: args.candidate_limit]],
            "selected": [describe(item) for item in row["selected_ids"]],
            "gold_rejection_reasons": row["gold_rejection_reasons"],
            "query_domains": row["query_domains"],
            "coverage": {
                "passed": row["coverage_passed"],
                "reason": row["coverage_reason"],
                "missing_terms": row["coverage_missing_terms"],
            },
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
