from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


DEFAULT_SOURCE = Path("docs/evaluation/EVAL_REGRESSION_V3.json")
DEFAULT_OUTPUT = Path("docs/evaluation/EVAL_REGRESSION_V3_1.json")
DEFAULT_AUDIT = Path("docs/evaluation/EVAL_REGRESSION_V3_1_MIGRATION.md")

# The original v3 Gold was annotated before documents 7 and 8 were rebuilt.
# Their old ranges are recorded here once so the immutable v3 file remains untouched.
LEGACY_LAYOUT = {
    "AUTOSAR Blockset User Guide R2024a": {"old_min": 117, "count": 837},
    "MathWorks_Simulink_Getting_Started_R2026a": {"old_min": 954, "count": 70},
}


def digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def evidence_ref(row: sqlite3.Row) -> dict:
    return {
        "key": row["qdrant_id"],
        "document_sha256": row["document_sha256"],
        "document_title": row["document_title"],
        "ordinal": row["ordinal"],
        "content_sha256": digest(row["content"]),
        "page": row["page"],
        "heading_path": row["heading_path"],
    }


def current_row(connection: sqlite3.Connection, evidence_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT e.id, e.ordinal, e.qdrant_id, e.content, e.page, e.heading_path,
               d.id AS document_id, d.title AS document_title, d.sha256 AS document_sha256
        FROM evidence_chunks e JOIN kb_documents d ON d.id=e.document_id
        WHERE e.id=?
        """,
        (evidence_id,),
    ).fetchone()


def migrated_row(
    connection: sqlite3.Connection,
    legacy_id: int,
    expected_docs: list[str],
) -> tuple[sqlite3.Row, bool]:
    row = current_row(connection, legacy_id)
    if row is not None and row["document_title"] in expected_docs:
        return row, False

    candidates = []
    for title in expected_docs:
        layout = LEGACY_LAYOUT.get(title)
        if not layout:
            continue
        ordinal = legacy_id - int(layout["old_min"])
        if 0 <= ordinal < int(layout["count"]):
            candidate = connection.execute(
                """
                SELECT e.id, e.ordinal, e.qdrant_id, e.content, e.page, e.heading_path,
                       d.id AS document_id, d.title AS document_title, d.sha256 AS document_sha256
                FROM evidence_chunks e JOIN kb_documents d ON d.id=e.document_id
                WHERE d.title=? AND e.ordinal=?
                """,
                (title, ordinal),
            ).fetchone()
            if candidate:
                candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError(
            f"legacy evidence E:{legacy_id} resolved to {len(candidates)} rows for {expected_docs}"
        )
    return candidates[0], True


def migrate(source: Path, output: Path, audit: Path, database: Path) -> dict:
    raw = source.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    original_sha = hashlib.sha256(raw).hexdigest()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    mappings: list[dict] = []

    for case in payload["cases"]:
        if case["expected_mode"] != "rag":
            case["legacy_gold_evidence"] = []
            case["gold_evidence_refs"] = []
            continue
        legacy_ids = [int(item) for item in case["gold_evidence"]]
        current_ids: list[int] = []
        refs: list[dict] = []
        for legacy_id in legacy_ids:
            row, changed = migrated_row(connection, legacy_id, case["expected_docs"])
            current_ids.append(int(row["id"]))
            refs.append(evidence_ref(row))
            mappings.append({
                "case_id": case["id"],
                "legacy_id": legacy_id,
                "current_id": int(row["id"]),
                "changed": changed,
                "document": row["document_title"],
                "ordinal": int(row["ordinal"]),
                "page": row["page"],
                "heading_path": row["heading_path"],
            })
        case["legacy_gold_evidence"] = legacy_ids
        case["gold_evidence"] = current_ids
        case["gold_evidence_refs"] = refs
        case["gold_migration"] = "stable_ref_verified_after_document_reindex"

    payload["version"] = "3.1"
    payload["status"] = "development_regression_v3_1"
    payload["derived_from"] = {
        "file": source.as_posix(),
        "sha256": original_sha,
        "version": "3.0",
    }
    payload["usage_policy"] = "repeatable_development_regression; not an unseen holdout"
    payload["evidence_reference_policy"] = "qdrant_uuid_plus_document_sha_ordinal_content_hash"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    changed = [item for item in mappings if item["changed"]]
    lines = [
        "# Evaluation Regression v3.1 Migration Audit",
        "",
        f"- Source: `{source.as_posix()}`",
        f"- Source SHA-256: `{original_sha}`",
        f"- Cases: {len(payload['cases'])}",
        f"- Gold references: {len(mappings)}",
        f"- Reindexed Gold IDs remapped: {len(changed)}",
        "- Policy: the original v3 and its formal reports remain immutable; v3.1 is a development regression set.",
        "",
        "## Remapped evidence",
        "",
        "| Case | Old ID | Current ID | Document | Ordinal | Page | Heading |",
        "|---|---:|---:|---|---:|---:|---|",
    ]
    for item in changed:
        heading = str(item["heading_path"] or "").replace("|", "\\|")
        lines.append(
            f"| {item['case_id']} | {item['legacy_id']} | {item['current_id']} | "
            f"{item['document']} | {item['ordinal']} | {item['page'] or '-'} | {heading} |"
        )
    audit.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "source_sha256": original_sha,
        "cases": len(payload["cases"]),
        "gold_references": len(mappings),
        "unique_stable_keys": len({ref["key"] for case in payload["cases"] for ref in case["gold_evidence_refs"]}),
        "remapped_ids": len(changed),
        "output": str(output),
        "audit": str(audit),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate frozen v3 Gold IDs to stable evidence references.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.output, args.audit, args.database), ensure_ascii=False, indent=2))
