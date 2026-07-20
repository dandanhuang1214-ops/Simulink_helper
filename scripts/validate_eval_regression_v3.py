from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


DEFAULT_PATH = Path("docs/evaluation/EVAL_REGRESSION_V3.json")
EXPECTED_DOMAINS = {"simulink", "autosar", "stateflow", "testing", "coverage", "cross_domain"}
ALLOWED_MODES = {"rag", "refusal"}


def validate(path: Path = DEFAULT_PATH, database: Path | None = None) -> dict:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    cases = payload.get("cases")
    assert payload.get("version") == "3.0", "version must be 3.0"
    assert payload.get("status") == "frozen_v3_holdout", "v3 must be frozen before blind evaluation"
    assert payload.get("annotation_policy") == "source_first_no_retrieval_labels"
    assert isinstance(cases, list) and len(cases) == 30, f"expected 30 cases, got {len(cases or [])}"

    ids = [case.get("id") for case in cases]
    questions = [case.get("question") for case in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert len(questions) == len(set(questions)), "questions must be unique"

    domains = Counter(case.get("domain") for case in cases)
    assert set(domains) == EXPECTED_DOMAINS, f"unexpected domains: {sorted(domains)}"
    assert all(domains[domain] == 5 for domain in EXPECTED_DOMAINS), domains
    modes = Counter(case.get("expected_mode") for case in cases)
    assert modes == Counter({"rag": 24, "refusal": 6}), modes

    for case in cases:
        case_id = case.get("id")
        assert case.get("expected_mode") in ALLOWED_MODES, f"{case_id}: invalid mode"
        assert case.get("type"), f"{case_id}: type required"
        assert case.get("query_style"), f"{case_id}: query style required"
        assert isinstance(case.get("must_cover"), list) and case["must_cover"], f"{case_id}: must_cover required"
        assert isinstance(case.get("forbidden"), list) and case["forbidden"], f"{case_id}: forbidden required"
        assert case.get("review_status") == "keep", f"{case_id}: frozen cases must be kept"
        evidence = case.get("gold_evidence")
        assert isinstance(evidence, list), f"{case_id}: gold_evidence must be a list"
        if case["expected_mode"] == "rag":
            assert case.get("expected_docs"), f"{case_id}: expected docs required"
            assert case.get("expected_sections"), f"{case_id}: expected sections required"
            assert case.get("gold_status") == "annotated_source", f"{case_id}: source annotation required"
            assert evidence and all(isinstance(item, int) and item > 0 for item in evidence), f"{case_id}: valid Gold ids required"
            assert case.get("gold_notes"), f"{case_id}: Gold notes required"
        else:
            assert case.get("expected_docs") == [], f"{case_id}: refusal docs must be empty"
            assert case.get("expected_sections") == [], f"{case_id}: refusal sections must be empty"
            assert case.get("gold_status") == "not_applicable", f"{case_id}: refusal Gold must be N/A"
            assert evidence == [], f"{case_id}: refusal Gold must be empty"

    result = {
        "version": payload["version"],
        "status": payload["status"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cases": len(cases),
        "domains": dict(sorted(domains.items())),
        "modes": dict(sorted(modes.items())),
        "annotated": sum(case.get("gold_status") == "annotated_source" for case in cases),
    }
    if database is not None:
        connection = sqlite3.connect(database)
        gold_ids = sorted({item for case in cases for item in case["gold_evidence"]})
        placeholders = ",".join("?" for _ in gold_ids)
        rows = connection.execute(
            f"""
            SELECT e.id, e.document_id, d.title
            FROM evidence_chunks e
            JOIN kb_documents d ON d.id = e.document_id
            WHERE e.id IN ({placeholders})
            """,
            gold_ids,
        ).fetchall()
        evidence_documents = {row[0]: (row[1], row[2]) for row in rows}
        missing = sorted(set(gold_ids) - set(evidence_documents))
        assert not missing, f"Gold evidence ids do not exist: {missing}"
        for case in cases:
            unexpected = {
                evidence_id: evidence_documents[evidence_id]
                for evidence_id in case["gold_evidence"]
                if evidence_documents[evidence_id][1] not in case["expected_docs"]
            }
            assert not unexpected, f"{case['id']}: Gold belongs to unexpected documents: {unexpected}"
        result["database_checked"] = str(database)
        result["unique_gold_evidence"] = len(gold_ids)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate the frozen regression v3 holdout set.")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.path, args.database), ensure_ascii=False, indent=2))
