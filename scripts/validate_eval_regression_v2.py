from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


DEFAULT_PATH = Path("docs/evaluation/EVAL_REGRESSION_V2.json")
EXPECTED_DOMAINS = {"simulink", "autosar", "stateflow", "testing", "coverage"}
ALLOWED_TYPES = {
    "definition", "definition_procedure", "relationship", "procedure",
    "comparison", "multi_hop", "boundary",
}
ALLOWED_MODES = {"rag", "refusal", "direct"}
DOMAIN_DOCUMENT_IDS = {
    "simulink": {1, 8},
    "autosar": {7},
    "stateflow": {8, 9},
    "testing": {10},
    "coverage": {11},
}


def validate(path: Path = DEFAULT_PATH, database: Path | None = None) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    assert isinstance(cases, list), "cases must be a list"
    assert len(cases) == 20, f"expected 20 cases, got {len(cases)}"

    ids = [case.get("id") for case in cases]
    questions = [case.get("question") for case in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert len(questions) == len(set(questions)), "questions must be unique"

    domains = Counter(case.get("domain") for case in cases)
    assert set(domains) == EXPECTED_DOMAINS, f"unexpected domains: {sorted(domains)}"
    assert all(domains[domain] == 4 for domain in EXPECTED_DOMAINS), domains

    for case in cases:
        case_id = case.get("id")
        assert case.get("type") in ALLOWED_TYPES, f"{case_id}: invalid type"
        assert case.get("expected_mode") in ALLOWED_MODES, f"{case_id}: invalid mode"
        assert isinstance(case.get("must_cover"), list) and case["must_cover"], f"{case_id}: must_cover required"
        assert isinstance(case.get("forbidden"), list) and case["forbidden"], f"{case_id}: forbidden required"
        assert case.get("review_status") in {"pending", "keep", "revise", "remove"}, f"{case_id}: invalid review status"
        gold_status = case.get("gold_status")
        gold_evidence = case.get("gold_evidence")
        assert isinstance(gold_evidence, list), f"{case_id}: gold evidence must be a list"
        if case["expected_mode"] == "rag":
            assert case.get("expected_docs"), f"{case_id}: RAG case requires expected docs"
            assert case.get("expected_sections"), f"{case_id}: RAG case requires source sections"
            assert gold_status in {"pending_source_annotation", "annotated_source"}, f"{case_id}: invalid gold status"
            if gold_status == "pending_source_annotation":
                assert gold_evidence == [], f"{case_id}: pending gold must stay empty"
            else:
                assert gold_evidence and all(isinstance(item, int) and item > 0 for item in gold_evidence), f"{case_id}: annotated gold requires ids"
                assert case.get("gold_notes"), f"{case_id}: annotated gold requires source notes"
        if case["expected_mode"] == "refusal":
            assert case.get("expected_docs") == [], f"{case_id}: refusal should not require evidence"
            assert case.get("gold_status") == "not_applicable", f"{case_id}: invalid refusal gold status"
            assert gold_evidence == [], f"{case_id}: refusal gold must be empty"

    if payload.get("status") == "frozen_v2_baseline":
        assert all(case.get("review_status") == "keep" for case in cases), "frozen set requires all cases kept"

    result = {
        "version": payload.get("version"),
        "cases": len(cases),
        "domains": dict(sorted(domains.items())),
        "rag": sum(case["expected_mode"] == "rag" for case in cases),
        "refusal": sum(case["expected_mode"] == "refusal" for case in cases),
        "annotated": sum(case.get("gold_status") == "annotated_source" for case in cases),
    }
    if database is not None:
        connection = sqlite3.connect(database)
        gold_ids = sorted({item for case in cases for item in case["gold_evidence"]})
        placeholders = ",".join("?" for _ in gold_ids)
        rows = connection.execute(
            f"SELECT id, document_id FROM evidence_chunks WHERE id IN ({placeholders})",
            gold_ids,
        ).fetchall()
        evidence_documents = {row[0]: row[1] for row in rows}
        missing = sorted(set(gold_ids) - set(evidence_documents))
        assert not missing, f"Gold evidence ids do not exist: {missing}"
        for case in cases:
            allowed_documents = DOMAIN_DOCUMENT_IDS[case["domain"]]
            invalid = {
                evidence_id: evidence_documents[evidence_id]
                for evidence_id in case["gold_evidence"]
                if evidence_documents[evidence_id] not in allowed_documents
            }
            assert not invalid, f"{case['id']}: Gold evidence belongs to unexpected documents: {invalid}"
        result["database_checked"] = str(database)
        result["unique_gold_evidence"] = len(gold_ids)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate the fixed regression v2 evaluation set.")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.path, args.database), ensure_ascii=False, indent=2))
