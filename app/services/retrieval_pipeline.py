from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.database import Document, EvidenceChunk, SessionLocal
from app.services.coverage import CoverageResult, assess_evidence_coverage, assess_question_preconditions
from app.services.domains import document_domains, preferred_domains
from app.services.evidence_selector import question_role, select_evidence
from app.services.question_aspects import aspect_evidence_score, evidence_aspect_coverage, requested_aspects
from app.services.retrieval import hybrid_search


@dataclass
class RetrievalPipelineResult:
    candidates: list[dict]
    evidence: list[dict]
    coverage: CoverageResult
    trace: dict
    fallback_used: bool = False


def _should_dense_fallback(profile: str, trace: dict, coverage: CoverageResult) -> bool:
    decision = trace.get("retrieval_decision") or {}
    low_confidence = int(decision.get("tier") or 0) >= 3
    return (
        profile == "fast"
        and trace.get("dense_skipped") is True
        and (not coverage.passed or low_confidence)
    )


def _should_online_rerank(*, requested: bool, enabled: bool, role: str, aspect_count: int) -> tuple[bool, str]:
    """Reserve the local LLM reranker for ambiguous multi-fact questions.

    ``hybrid_search`` still applies its confidence-tier check, so this policy is
    only an outer eligibility gate.  A relationship/comparison or a question
    with multiple declared aspects may be reranked when the deployment opts in;
    ordinary single-intent questions never pay the model-switch cost.
    """
    if not requested:
        return False, "request_disabled"
    if not enabled:
        return False, "local_rerank_disabled"
    if aspect_count >= 2:
        return True, "compound_aspects"
    if role in {"relationship", "comparison"}:
        return True, f"role:{role}"
    return False, "simple_single_intent"


def _heading_family(left: str | None, right: str | None) -> bool:
    left_parts = [item.strip().casefold() for item in (left or "").split("/") if item.strip()]
    right_parts = [item.strip().casefold() for item in (right or "").split("/") if item.strip()]
    if not left_parts or not right_parts:
        return True
    return left_parts[:2] == right_parts[:2]


def _expand_missing_aspect_neighbors(
    question: str,
    evidence: list[dict],
    *,
    final_limit: int,
    trace: dict,
) -> list[dict]:
    """Attach adjacent same-section chunks only when a declared aspect is missing.

    Neighbor chunks remain independent evidence items with their own ``E:n``
    citation.  This avoids attributing a neighbor's facts to the anchor chunk
    and keeps normal, already-covered questions unchanged.
    """
    covered, missing = evidence_aspect_coverage(question, evidence)
    if not missing or len(evidence) >= final_limit or not evidence:
        trace["neighbor_expansion"] = {
            "used": False,
            "reason": "already_covered_or_full" if not missing or len(evidence) >= final_limit else "no_evidence",
            "covered_aspects": covered,
            "missing_aspects": missing,
        }
        return evidence

    aspects = {item.name: item for item in requested_aspects(question) if item.name in missing}
    anchor_ids = [int(item["chunk_id"]) for item in evidence if item.get("chunk_id") is not None]
    with SessionLocal() as session:
        anchors = session.query(EvidenceChunk).filter(EvidenceChunk.id.in_(anchor_ids)).all()
        neighbor_ids = {
            int(value)
            for anchor in anchors
            for value in (anchor.previous_id, anchor.next_id)
            if value is not None
        } - set(anchor_ids)
        rows = (
            session.query(EvidenceChunk, Document)
            .join(Document)
            .filter(EvidenceChunk.id.in_(neighbor_ids), Document.enabled.is_(True))
            .all()
            if neighbor_ids else []
        )

    anchor_by_document = {}
    for item in evidence:
        anchor_by_document.setdefault(int(item.get("document_id") or 0), []).append(item)
    candidates: list[dict] = []
    for chunk, document in rows:
        anchors_for_document = anchor_by_document.get(int(document.id), [])
        if not any(_heading_family(anchor.get("heading_path"), chunk.heading_path) for anchor in anchors_for_document):
            continue
        candidate = {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "title": document.title,
            "document_domains": sorted(document_domains(document.title)),
            "content": chunk.content,
            "ordinal": chunk.ordinal,
            "page": chunk.page,
            "bbox": chunk.bbox_json,
            "heading_path": chunk.heading_path,
            "channels": ["neighbor"],
            "rrf_score": 0.0,
            "final_score": 0.0,
            "expansion_source": "same_section_neighbor",
        }
        candidate["aspect_scores"] = {
            name: round(aspect_evidence_score(aspect, candidate), 4)
            for name, aspect in aspects.items()
        }
        if max(candidate["aspect_scores"].values(), default=0.0) >= 0.28:
            candidates.append(candidate)

    expanded = list(evidence)
    added: list[int] = []
    for name in missing:
        best = max(
            (item for item in candidates if item not in expanded),
            key=lambda item: item.get("aspect_scores", {}).get(name, 0.0),
            default=None,
        )
        if best and best.get("aspect_scores", {}).get(name, 0.0) >= 0.28 and len(expanded) < final_limit:
            expanded.append(best)
            added.append(int(best["chunk_id"]))

    final_covered, final_missing = evidence_aspect_coverage(question, expanded)
    trace["neighbor_expansion"] = {
        "used": bool(added),
        "reason": "missing_aspect_neighbor" if added else "no_matching_same_section_neighbor",
        "added_chunk_ids": added,
        "covered_aspects": final_covered,
        "missing_aspects": final_missing,
    }
    return expanded


async def retrieve_evidence_with_coverage(
    question: str,
    *,
    use_rewrite: bool = False,
    use_rerank: bool = True,
    retrieval_profile: str | None = None,
    document_ids: list[int] | None = None,
    releases: list[str] | None = None,
    trace: dict | None = None,
) -> RetrievalPipelineResult:
    """Run retrieval, evidence selection, coverage gate, and optional Dense fallback.

    The default local profile is optimized for a 4GB GPU: it may skip Dense when
    lexical/Wiki evidence looks strong. If that fast path later fails coverage,
    we retry once with the full profile before refusing. This keeps ordinary
    questions fast while avoiding false refusals caused by an over-aggressive
    fast path.
    """
    settings = get_settings()
    profile = (retrieval_profile or settings.retrieval_profile or "fast").lower()
    working_trace: dict = trace if trace is not None else {}
    precondition = assess_question_preconditions(question)
    if precondition is not None:
        working_trace["preflight"] = {
            "passed": False,
            "reason": precondition.reason,
            "missing_terms": precondition.missing_terms,
        }
        return RetrievalPipelineResult([], [], precondition, working_trace, False)
    role = question_role(question, preferred_domains(question))
    candidate_limit = settings.evidence_candidate_k
    if role in {"procedure", "definition_procedure"}:
        candidate_limit = max(candidate_limit, 30)
    aspects = requested_aspects(question)
    if len(aspects) >= 2:
        # Keep a bounded pool for each requested aspect. The selector still
        # emits only the configured final evidence count, so this improves
        # compound-question recall without enlarging the answer context.
        candidate_limit = max(candidate_limit, min(48, len(aspects) * 16))

    allow_online_rerank, rerank_policy_reason = _should_online_rerank(
        requested=use_rerank,
        enabled=settings.llm_rerank_enabled,
        role=role,
        aspect_count=len(aspects),
    )
    working_trace["online_rerank_policy"] = {
        "eligible": allow_online_rerank,
        "reason": rerank_policy_reason,
        "role": role,
        "aspects": [item.name for item in aspects],
    }
    candidates = await hybrid_search(
        question,
        limit=candidate_limit,
        use_rewrite=use_rewrite,
        use_rerank=allow_online_rerank,
        retrieval_profile=profile,
        document_ids=document_ids,
        releases=releases,
        trace=working_trace,
    )
    evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=working_trace)
    evidence = _expand_missing_aspect_neighbors(
        question,
        evidence,
        final_limit=settings.evidence_final_k,
        trace=working_trace,
    )
    coverage = assess_evidence_coverage(question, evidence)

    if not _should_dense_fallback(profile, working_trace, coverage):
        return RetrievalPipelineResult(candidates, evidence, coverage, working_trace, False)

    first_trace = dict(working_trace)
    fallback_trace: dict = {}
    fallback_candidates = await hybrid_search(
        question,
        limit=candidate_limit,
        use_rewrite=use_rewrite,
        use_rerank=allow_online_rerank,
        retrieval_profile="full",
        document_ids=document_ids,
        releases=releases,
        trace=fallback_trace,
    )
    fallback_evidence = select_evidence(
        question,
        fallback_candidates,
        final_limit=settings.evidence_final_k,
        trace=fallback_trace,
    )
    fallback_evidence = _expand_missing_aspect_neighbors(
        question,
        fallback_evidence,
        final_limit=settings.evidence_final_k,
        trace=fallback_trace,
    )
    fallback_coverage = assess_evidence_coverage(question, fallback_evidence)

    working_trace.clear()
    working_trace.update(fallback_trace if fallback_coverage.passed else first_trace)
    working_trace["online_rerank_policy"] = {
        "eligible": allow_online_rerank,
        "reason": rerank_policy_reason,
        "role": role,
        "aspects": [item.name for item in aspects],
    }
    working_trace["dense_fallback"] = {
        "used": True,
        "reason": "fast_confidence_or_coverage_failed_after_dense_skip",
        "first_profile": profile,
        "first_coverage": {
            "passed": coverage.passed,
            "missing_terms": coverage.missing_terms,
            "reason": coverage.reason,
        },
        "fallback_profile": "full",
        "fallback_coverage": {
            "passed": fallback_coverage.passed,
            "missing_terms": fallback_coverage.missing_terms,
            "reason": fallback_coverage.reason,
        },
    }

    if fallback_coverage.passed:
        return RetrievalPipelineResult(
            fallback_candidates,
            fallback_evidence,
            fallback_coverage,
            working_trace,
            True,
        )
    return RetrievalPipelineResult(candidates, evidence, coverage, working_trace, True)
