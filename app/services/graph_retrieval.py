from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass

from app.database import EvidenceChunk, GraphEntity, GraphRelation, SessionLocal
from app.services.domains import document_domains, preferred_domains
from app.services.text import lexical_tokens


EVIDENCE_REF_RE = re.compile(r"E\s*:\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class GraphCandidate:
    chunk_id: int
    score: float
    reasons: tuple[str, ...]
    relation_ids: tuple[int, ...]


def evidence_ids_from_refs(value: str | list | None) -> set[int]:
    """Parse graph evidence refs like ["E:12", "E:34"] into chunk ids."""
    if value is None:
        return set()
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            loaded = [value]
    else:
        loaded = value
    ids: set[int] = set()
    if not isinstance(loaded, list):
        return ids
    for item in loaded:
        match = EVIDENCE_REF_RE.search(str(item))
        if match:
            ids.add(int(match.group(1)))
    return ids


def _entity_matches_query(entity: GraphEntity, query: str, query_tokens: set[str]) -> bool:
    label = (entity.label or "").strip().lower()
    if not label:
        return False
    query_lower = query.lower()
    if len(label) >= 3 and label in query_lower:
        return True
    label_tokens = {item for item in lexical_tokens(label) if len(item) >= 4}
    strong_query_tokens = {item for item in query_tokens if len(item) >= 4}
    if label_tokens and label_tokens & strong_query_tokens:
        return True
    try:
        aliases = json.loads(entity.aliases_json or "[]")
    except json.JSONDecodeError:
        aliases = []
    for alias in aliases if isinstance(aliases, list) else []:
        alias_lower = str(alias).strip().lower()
        if len(alias_lower) >= 3 and alias_lower in query_lower:
            return True
    return False


def graph_candidate_scores(
    *,
    query: str,
    seed_chunk_ids: list[int],
    allowed_documents: set[int] | None = None,
    limit: int = 24,
) -> tuple[dict[int, GraphCandidate], dict]:
    """Return one-hop graph expansion candidates for the current retrieval seeds.

    The graph is treated as a secondary recall signal, not as ground truth:
    - seed evidence -> relation -> neighboring evidence
    - query entity mention -> incident relation -> evidence

    The returned scores are intentionally small so BM25/Dense/domain routing still
    decide the main ranking. This keeps the demo stable while making the graph
    operational.
    """
    seed_ids = {int(item) for item in seed_chunk_ids if item}
    if not seed_ids:
        return {}, {"enabled": True, "used": False, "reason": "no_seed_chunks"}

    query_domains = preferred_domains(query)
    query_tokens = set(lexical_tokens(query))
    scores: dict[int, float] = defaultdict(float)
    reasons: dict[int, set[str]] = defaultdict(set)
    relation_ids: dict[int, set[int]] = defaultdict(set)

    with SessionLocal() as session:
        relation_count = session.query(GraphRelation).count()
        if relation_count == 0:
            return {}, {"enabled": True, "used": False, "reason": "graph_empty"}

        entities = {item.id: item for item in session.query(GraphEntity).all()}
        matched_entity_ids = {
            entity.id
            for entity in entities.values()
            if _entity_matches_query(entity, query, query_tokens)
        }
        relations = session.query(GraphRelation).all()

        for relation in relations:
            refs = evidence_ids_from_refs(relation.evidence_refs_json)
            if not refs:
                continue

            touches_seed = bool(refs & seed_ids)
            touches_query_entity = (
                relation.source_entity_id in matched_entity_ids
                or relation.target_entity_id in matched_entity_ids
            )
            # Demo v1 keeps graph retrieval conservative: the graph may expand
            # from already-retrieved evidence, but entity mentions alone should
            # not introduce a separate broad retrieval path yet.
            if not touches_seed:
                continue

            for chunk_id in refs:
                if chunk_id in seed_ids:
                    continue
                if touches_seed:
                    scores[chunk_id] += 0.012 * min(1.2, max(0.5, float(relation.weight or 1.0)))
                    reasons[chunk_id].add("seed_relation")
                if touches_query_entity:
                    scores[chunk_id] += 0.006 * min(1.0, max(0.5, float(relation.confidence or 0.7)))
                    reasons[chunk_id].add("query_entity")
                relation_ids[chunk_id].add(int(relation.id))

        if not scores:
            return {}, {
                "enabled": True,
                "used": False,
                "reason": "no_related_evidence",
                "seed_chunk_ids": sorted(seed_ids)[:12],
                "matched_entities": len(matched_entity_ids),
            }

        rows = session.query(EvidenceChunk.id, EvidenceChunk.document_id).filter(EvidenceChunk.id.in_(scores.keys())).all()
        document_by_chunk = {int(row.id): int(row.document_id) for row in rows}

        if allowed_documents is not None:
            for chunk_id, document_id in list(document_by_chunk.items()):
                if document_id not in allowed_documents:
                    scores.pop(chunk_id, None)

        if query_domains:
            doc_rows = session.query(EvidenceChunk.id, EvidenceChunk.document_id).filter(EvidenceChunk.id.in_(scores.keys())).all()
            doc_ids = {int(row.document_id) for row in doc_rows}
            title_by_doc = {}
            if doc_ids:
                from app.database import Document

                title_by_doc = {int(row.id): row.title for row in session.query(Document.id, Document.title).filter(Document.id.in_(doc_ids)).all()}
            for row in doc_rows:
                doc_domain = document_domains(title_by_doc.get(int(row.document_id), ""))
                if doc_domain & query_domains:
                    scores[int(row.id)] += 0.003
                    reasons[int(row.id)].add("domain_aligned")

    capped_scores = {chunk_id: min(score, 0.035) for chunk_id, score in scores.items()}
    ordered = sorted(capped_scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    candidates = {
        chunk_id: GraphCandidate(
            chunk_id=chunk_id,
            score=round(score, 6),
            reasons=tuple(sorted(reasons[chunk_id])),
            relation_ids=tuple(sorted(relation_ids[chunk_id])[:8]),
        )
        for chunk_id, score in ordered
    }
    return candidates, {
        "enabled": True,
        "used": bool(candidates),
        "reason": "expanded" if candidates else "filtered_empty",
        "seed_chunk_ids": sorted(seed_ids)[:12],
        "matched_entities": len(matched_entity_ids),
        "candidate_count": len(candidates),
        "candidates": [
            {
                "chunk_id": item.chunk_id,
                "score": item.score,
                "reasons": list(item.reasons),
                "relation_ids": list(item.relation_ids),
            }
            for item in candidates.values()
        ],
    }
