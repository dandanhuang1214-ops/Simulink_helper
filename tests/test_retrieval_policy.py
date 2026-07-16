from app.services.retrieval_policy import (
    assess_retrieval_confidence,
    candidate_similarity,
    diversify_candidates,
)


def _candidate(
    chunk_id: int,
    content: str,
    *,
    document_id: int = 1,
    heading: str = "",
    score: float = 0.1,
    channels: list[str] | None = None,
    domains: list[str] | None = None,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "title": "AUTOSAR Guide",
        "heading_path": heading,
        "content": content,
        "final_score": score,
        "rrf_score": score,
        "channels": channels or ["bm25", "dense"],
        "document_domains": domains or ["autosar"],
    }


def test_candidate_similarity_detects_repeated_passages() -> None:
    left = _candidate(1, "Import AUTOSAR XML descriptions into Simulink using an ARXML importer.")
    right = _candidate(2, "Import AUTOSAR XML descriptions into Simulink using the ARXML importer.")
    assert candidate_similarity(left, right) > 0.7


def test_diversify_candidates_removes_near_duplicate_same_section() -> None:
    candidates = [
        _candidate(1, "Import AUTOSAR XML descriptions into Simulink using an ARXML importer.", heading="Import XML"),
        _candidate(2, "Import AUTOSAR XML descriptions into Simulink using the ARXML importer.", heading="Import XML"),
        _candidate(3, "Map AUTOSAR ports and runnables to Simulink model elements.", heading="Map Components"),
    ]
    diversified, trace = diversify_candidates(candidates)
    assert [item["chunk_id"] for item in diversified] == [1, 3]
    assert trace["duplicate_count"] == 1


def test_confidence_uses_observable_retrieval_signals() -> None:
    candidates = [
        _candidate(
            index,
            "AUTOSAR ARXML import into Simulink component model",
            score=0.2 - index * 0.02,
        )
        for index in range(1, 7)
    ]
    decision = assess_retrieval_confidence(
        "How to import AUTOSAR ARXML into Simulink?",
        candidates,
        duplicate_ratio=0.0,
    )
    assert decision.tier in {1, 2}
    assert decision.confidence >= 0.48


def test_low_confidence_candidates_choose_tier_three() -> None:
    candidates = [
        _candidate(
            index,
            f"Unrelated generic material {index}",
            score=0.1,
            channels=["bm25"],
            domains=["simulink"],
        )
        for index in range(1, 7)
    ]
    decision = assess_retrieval_confidence(
        "How to import AUTOSAR ARXML into Simulink?",
        candidates,
        duplicate_ratio=0.5,
    )
    assert decision.tier == 3
    assert decision.mode == "dense_fallback"
    assert "retrieval_channels_disagree" in decision.reasons


def test_conflicting_relevant_candidates_choose_rerank() -> None:
    candidates = [
        _candidate(
            index,
            "AUTOSAR ARXML import Simulink component",
            score=0.1,
            channels=["bm25"],
            domains=["autosar"],
        )
        for index in range(1, 7)
    ]
    decision = assess_retrieval_confidence(
        "AUTOSAR ARXML import Simulink component mapping steps",
        candidates,
        duplicate_ratio=0.5,
    )
    assert decision.tier == 3
    assert decision.mode == "rerank"
