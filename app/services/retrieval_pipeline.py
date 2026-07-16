from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.services.coverage import CoverageResult, assess_evidence_coverage
from app.services.domains import preferred_domains
from app.services.evidence_selector import question_role, select_evidence
from app.services.retrieval import hybrid_search


@dataclass
class RetrievalPipelineResult:
    candidates: list[dict]
    evidence: list[dict]
    coverage: CoverageResult
    trace: dict
    fallback_used: bool = False


def _should_dense_fallback(profile: str, trace: dict, coverage: CoverageResult) -> bool:
    return (
        profile == "fast"
        and not coverage.passed
        and trace.get("dense_skipped") is True
    )


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
    role = question_role(question, preferred_domains(question))
    candidate_limit = settings.evidence_candidate_k
    if role == "procedure":
        candidate_limit = max(candidate_limit, 30)

    candidates = await hybrid_search(
        question,
        limit=candidate_limit,
        use_rewrite=use_rewrite,
        use_rerank=use_rerank,
        retrieval_profile=profile,
        document_ids=document_ids,
        releases=releases,
        trace=working_trace,
    )
    evidence = select_evidence(question, candidates, final_limit=settings.evidence_final_k, trace=working_trace)
    coverage = assess_evidence_coverage(question, evidence)

    if not _should_dense_fallback(profile, working_trace, coverage):
        return RetrievalPipelineResult(candidates, evidence, coverage, working_trace, False)

    first_trace = dict(working_trace)
    fallback_trace: dict = {}
    fallback_candidates = await hybrid_search(
        question,
        limit=candidate_limit,
        use_rewrite=use_rewrite,
        use_rerank=use_rerank,
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
    fallback_coverage = assess_evidence_coverage(question, fallback_evidence)

    working_trace.clear()
    working_trace.update(fallback_trace if fallback_coverage.passed else first_trace)
    working_trace["dense_fallback"] = {
        "used": True,
        "reason": "fast_coverage_failed_after_dense_skip",
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
