from __future__ import annotations

import hashlib

from app.database import Document, EvidenceChunk, SessionLocal


def evidence_content_sha256(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def stable_evidence_ref(chunk: EvidenceChunk, document: Document) -> dict:
    return {
        "key": chunk.qdrant_id,
        "document_sha256": document.sha256,
        "document_title": document.title,
        "ordinal": chunk.ordinal,
        "content_sha256": evidence_content_sha256(chunk.content),
        "page": chunk.page,
        "heading_path": chunk.heading_path,
    }


def resolve_gold_evidence(case: dict) -> list[int]:
    refs = case.get("gold_evidence_refs")
    if refs is None:
        return [int(item) for item in case.get("gold_evidence", [])]
    if not isinstance(refs, list):
        raise ValueError(f"{case.get('id', 'unknown')}: gold_evidence_refs must be a list")
    if not refs:
        return []

    keys = [str(item.get("key") or "") for item in refs]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError(f"{case.get('id', 'unknown')}: stable evidence keys must be non-empty and unique")

    with SessionLocal() as session:
        rows = (
            session.query(EvidenceChunk, Document)
            .join(Document)
            .filter(EvidenceChunk.qdrant_id.in_(keys))
            .all()
        )
    by_key = {chunk.qdrant_id: (chunk, document) for chunk, document in rows}
    resolved: list[int] = []
    for ref in refs:
        key = str(ref["key"])
        row = by_key.get(key)
        if not row:
            raise ValueError(f"{case.get('id', 'unknown')}: stable Gold evidence is missing: {key}")
        chunk, document = row
        checks = {
            "document_sha256": document.sha256,
            "ordinal": chunk.ordinal,
            "content_sha256": evidence_content_sha256(chunk.content),
            "page": chunk.page,
            "heading_path": chunk.heading_path,
        }
        mismatches = {
            name: {"expected": ref.get(name), "actual": actual}
            for name, actual in checks.items()
            if ref.get(name) != actual
        }
        if mismatches:
            raise ValueError(
                f"{case.get('id', 'unknown')}: stable Gold evidence changed for {key}: {mismatches}"
            )
        resolved.append(int(chunk.id))
    return resolved
