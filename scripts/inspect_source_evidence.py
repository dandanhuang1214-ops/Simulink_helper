from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect source-document chunks without running retrieval.")
    parser.add_argument("--database", type=Path, default=Path("/app/data/app.db"))
    parser.add_argument("--document", type=int, action="append", required=True)
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--chars", type=int, default=900)
    parser.add_argument("--min-page", type=int, default=0)
    parser.add_argument("--all-terms", action="store_true")
    parser.add_argument("--sort-matches", action="store_true")
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in args.document)
    rows = connection.execute(
        f"""
        SELECT id, document_id, ordinal, page, heading_path, content
        FROM evidence_chunks
        WHERE document_id IN ({placeholders})
        ORDER BY document_id, ordinal
        """,
        args.document,
    ).fetchall()

    terms = [term.casefold() for term in args.term]
    output = []
    for row in rows:
        if int(row["page"] or 0) < args.min_page:
            continue
        haystack = f"{row['heading_path']}\n{row['content']}".casefold()
        matched = [term for term in terms if term in haystack]
        if terms and (not matched or (args.all_terms and len(matched) != len(terms))):
            continue
        output.append({
            "id": row["id"],
            "document_id": row["document_id"],
            "ordinal": row["ordinal"],
            "page": row["page"],
            "heading_path": row["heading_path"],
            "matched_terms": matched,
            "content": row["content"][: args.chars],
        })
    if args.sort_matches:
        output.sort(key=lambda item: (-len(item["matched_terms"]), item["ordinal"]))
    output = output[: args.limit]

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
