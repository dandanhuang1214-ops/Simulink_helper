from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


DEFAULT_PATH = Path("docs/evaluation/EVAL_REGRESSION_V3_1.json")
EXPECTED_DOMAINS = {"simulink", "autosar", "stateflow", "testing", "coverage", "cross_domain"}


def digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def validate(path: Path, database: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") or []
    assert payload.get("version") == "3.1"
    assert payload.get("status") == "development_regression_v3_1"
    assert len(cases) == 30
    assert Counter(case["domain"] for case in cases) == Counter({domain: 5 for domain in EXPECTED_DOMAINS})
    assert Counter(case["expected_mode"] for case in cases) == Counter({"rag": 24, "refusal": 6})

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    keys: set[str] = set()
    for case in cases:
        refs = case.get("gold_evidence_refs")
        assert isinstance(refs, list), f"{case['id']}: stable refs required"
        if case["expected_mode"] == "refusal":
            assert refs == [] and case["gold_evidence"] == []
            continue
        assert len(refs) == len(case["gold_evidence"]) > 0
        resolved_ids: list[int] = []
        for ref in refs:
            row = connection.execute(
                """
                SELECT e.id, e.ordinal, e.content, e.page, e.heading_path,
                       d.title, d.sha256
                FROM evidence_chunks e JOIN kb_documents d ON d.id=e.document_id
                WHERE e.qdrant_id=?
                """,
                (ref["key"],),
            ).fetchone()
            assert row is not None, f"{case['id']}: missing stable key {ref['key']}"
            assert row["sha256"] == ref["document_sha256"]
            assert row["title"] == ref["document_title"]
            assert row["ordinal"] == ref["ordinal"]
            assert digest(row["content"]) == ref["content_sha256"]
            assert row["page"] == ref["page"]
            assert row["heading_path"] == ref["heading_path"]
            resolved_ids.append(int(row["id"]))
            keys.add(ref["key"])
        assert resolved_ids == case["gold_evidence"], f"{case['id']}: compatibility IDs are stale"

    return {
        "version": payload["version"],
        "status": payload["status"],
        "cases": len(cases),
        "rag_cases": sum(case["expected_mode"] == "rag" for case in cases),
        "refusal_cases": sum(case["expected_mode"] == "refusal" for case in cases),
        "unique_stable_keys": len(keys),
        "database_checked": str(database),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate the stable-reference v3.1 regression set.")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.path, args.database), ensure_ascii=False, indent=2))
