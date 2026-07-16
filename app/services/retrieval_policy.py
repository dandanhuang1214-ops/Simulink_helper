from __future__ import annotations

from dataclasses import dataclass

from app.services.domains import preferred_domains
from app.services.text import lexical_tokens


@dataclass(frozen=True)
class RetrievalDecision:
    tier: int
    mode: str
    confidence: float
    reasons: list[str]
    signals: dict[str, float]


def _token_set(value: str) -> set[str]:
    return {
        token
        for token in lexical_tokens((value or "")[:1800])[:256]
        if len(token) >= 2
    }


def _fingerprint(candidate: dict) -> tuple[set[str], str]:
    tokens = _token_set(
        f"{candidate.get('heading_path') or ''} {candidate.get('content') or ''}"
    )
    heading = (candidate.get("heading_path") or "").strip().lower()
    return tokens, heading


def _fingerprint_similarity(
    left: tuple[set[str], str],
    right: tuple[set[str], str],
) -> float:
    left_tokens, left_heading = left
    right_tokens, right_heading = right
    if not left_tokens or not right_tokens:
        return 0.0
    lexical = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    if left_heading and left_heading == right_heading:
        lexical = max(lexical, 0.72)
    return min(1.0, lexical)


def candidate_similarity(left: dict, right: dict) -> float:
    return _fingerprint_similarity(_fingerprint(left), _fingerprint(right))


def diversify_candidates(
    candidates: list[dict],
    *,
    limit: int | None = None,
    relevance_weight: float = 0.84,
) -> tuple[list[dict], dict]:
    """Remove near duplicates and apply conservative lexical MMR ordering."""
    if not candidates:
        return [], {
            "enabled": True,
            "input_count": 0,
            "deduplicated_count": 0,
            "duplicate_count": 0,
            "duplicate_ratio": 0.0,
            "reordered": False,
        }

    unique: list[dict] = []
    unique_fingerprints: list[tuple[set[str], str]] = []
    duplicates: list[dict] = []
    seen_ids: set[int] = set()
    for rank, candidate in enumerate(candidates, start=1):
        chunk_id = int(candidate.get("chunk_id") or 0)
        if chunk_id and chunk_id in seen_ids:
            duplicates.append({"chunk_id": chunk_id, "rank": rank, "reason": "same_chunk"})
            continue

        duplicate_of: int | None = None
        candidate_fingerprint = _fingerprint(candidate)
        for kept, kept_fingerprint in zip(unique, unique_fingerprints):
            similarity = _fingerprint_similarity(candidate_fingerprint, kept_fingerprint)
            same_document = candidate.get("document_id") == kept.get("document_id")
            same_heading = bool(
                candidate.get("heading_path")
                and candidate.get("heading_path") == kept.get("heading_path")
            )
            threshold = 0.86 if same_document else 0.94
            if same_heading:
                threshold = min(threshold, 0.80)
            if similarity >= threshold:
                duplicate_of = int(kept.get("chunk_id") or 0)
                duplicates.append({
                    "chunk_id": chunk_id,
                    "rank": rank,
                    "reason": "near_duplicate",
                    "duplicate_of": duplicate_of,
                    "similarity": round(similarity, 4),
                })
                break
        if duplicate_of is not None:
            continue
        copied = dict(candidate)
        copied["_original_rank"] = rank
        copied["_fingerprint"] = candidate_fingerprint
        unique.append(copied)
        unique_fingerprints.append(candidate_fingerprint)
        if chunk_id:
            seen_ids.add(chunk_id)

    target = min(limit or len(unique), len(unique))
    # Diversity affects the candidates that can reach the selector/reranker.
    # Reordering the entire 80-item diagnostic pool adds CPU cost but no value.
    mmr_window = min(24, target)
    head = unique[:mmr_window]
    tail = unique[mmr_window:target]
    if mmr_window <= 1:
        result = head + tail
    else:
        remaining = list(head)
        result = [remaining.pop(0)]
        per_document: dict[int, int] = {int(result[0].get("document_id") or 0): 1}
        while remaining and len(result) < mmr_window:
            pool_size = max(1, mmr_window - 1)

            def mmr_score(item: dict) -> float:
                original_rank = int(item.get("_original_rank") or mmr_window)
                relevance = 1.0 - ((original_rank - 1) / pool_size)
                similarity = max(
                    _fingerprint_similarity(item["_fingerprint"], selected["_fingerprint"])
                    for selected in result
                )
                document_id = int(item.get("document_id") or 0)
                concentration_penalty = min(0.08, per_document.get(document_id, 0) * 0.02)
                return (
                    relevance_weight * relevance
                    - (1.0 - relevance_weight) * similarity
                    - concentration_penalty
                )

            best = max(remaining, key=mmr_score)
            remaining.remove(best)
            result.append(best)
            document_id = int(best.get("document_id") or 0)
            per_document[document_id] = per_document.get(document_id, 0) + 1
        result.extend(tail)

    for item in result:
        item.pop("_original_rank", None)
        item.pop("_fingerprint", None)
    original_ids = [item.get("chunk_id") for item in unique[:target]]
    result_ids = [item.get("chunk_id") for item in result]
    return result, {
        "enabled": True,
        "input_count": len(candidates),
        "deduplicated_count": len(unique),
        "output_count": len(result),
        "duplicate_count": len(duplicates),
        "duplicate_ratio": round(len(duplicates) / max(1, len(candidates)), 4),
        "reordered": original_ids != result_ids,
        "duplicates": duplicates[:20],
    }


def assess_retrieval_confidence(
    query: str,
    candidates: list[dict],
    *,
    dense_skipped: bool = False,
    duplicate_ratio: float = 0.0,
) -> RetrievalDecision:
    """Choose a tier from observable retrieval signals, not benchmark labels."""
    if not candidates:
        return RetrievalDecision(
            tier=3,
            mode="rerank",
            confidence=0.0,
            reasons=["no_candidates"],
            signals={
                "lexical_coverage": 0.0,
                "channel_agreement": 0.0,
                "score_margin": 0.0,
                "domain_coverage": 0.0,
                "diversity": 0.0,
            },
        )

    top = candidates[: min(8, len(candidates))]
    query_tokens = _token_set(query)
    evidence_tokens: set[str] = set()
    for item in top[:3]:
        evidence_tokens.update(_token_set(
            f"{item.get('title') or ''} {item.get('heading_path') or ''} {item.get('content') or ''}"
        ))
    lexical_coverage = (
        len(query_tokens & evidence_tokens) / len(query_tokens)
        if query_tokens else 0.5
    )

    if dense_skipped:
        agreed = sum(1 for item in top if len(set(item.get("channels") or [])) >= 2)
    else:
        agreed = sum(
            1 for item in top
            if {"bm25", "dense"}.issubset(set(item.get("channels") or []))
        )
    channel_agreement = agreed / max(1, len(top))

    top_score = float(top[0].get("final_score", top[0].get("rrf_score", 0.0)) or 0.0)
    comparison_index = min(4, len(top) - 1)
    comparison_score = float(
        top[comparison_index].get("final_score", top[comparison_index].get("rrf_score", 0.0)) or 0.0
    )
    score_margin = (
        max(0.0, min(1.0, (top_score - comparison_score) / top_score))
        if top_score > 0 else 0.0
    )

    query_domains = set(preferred_domains(query))
    if not query_domains:
        domain_coverage = 1.0
    else:
        found_domains: set[str] = set()
        for item in top:
            found_domains.update(set(item.get("document_domains") or []) & query_domains)
        domain_coverage = len(found_domains) / len(query_domains)

    diversity = max(0.0, 1.0 - duplicate_ratio)
    confidence = (
        0.32 * lexical_coverage
        + 0.22 * channel_agreement
        + 0.14 * score_margin
        + 0.17 * domain_coverage
        + 0.15 * diversity
    )
    if len(candidates) < 4:
        confidence -= 0.12
    confidence = round(max(0.0, min(1.0, confidence)), 4)

    reasons: list[str] = []
    if lexical_coverage < 0.55:
        reasons.append("low_query_term_coverage")
    if channel_agreement < 0.25:
        reasons.append("retrieval_channels_disagree")
    if score_margin < 0.08:
        reasons.append("flat_candidate_scores")
    if domain_coverage < 1.0:
        reasons.append("incomplete_domain_coverage")
    if duplicate_ratio > 0.25:
        reasons.append("candidate_crowding")

    if confidence >= 0.72:
        tier, mode = 1, "direct"
    elif confidence >= 0.48:
        tier, mode = 2, "lightweight"
    else:
        tier = 3
        mode = "dense_fallback" if lexical_coverage < 0.20 else "rerank"
    if not reasons:
        reasons.append("signals_consistent")

    return RetrievalDecision(
        tier=tier,
        mode=mode,
        confidence=confidence,
        reasons=reasons,
        signals={
            "lexical_coverage": round(lexical_coverage, 4),
            "channel_agreement": round(channel_agreement, 4),
            "score_margin": round(score_margin, 4),
            "domain_coverage": round(domain_coverage, 4),
            "diversity": round(diversity, 4),
        },
    )
