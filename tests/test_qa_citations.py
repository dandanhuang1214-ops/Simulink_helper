from app.services.qa import ensure_evidence_citations


def test_definition_alignment_adds_citation_to_supported_uncited_sentence() -> None:
    evidence = [{
        "chunk_id": 1070,
        "document_id": 9,
        "title": "Stateflow User Guide",
        "page": 1,
        "bbox": None,
        "heading_path": "Stateflow Overview",
        "content": "MathWorks Stateflow is a graphical tool for modeling finite state machines (FSM) in Simulink.",
    }]
    answer = (
        "Stateflow 是 MathWorks 用于建模有限状态机（FSM）的图形化工具。"
        "它可以集成到 Simulink 模型中 [E:1070]。"
    )

    normalized, citations = ensure_evidence_citations(
        answer,
        evidence,
        require_sentence_citations=True,
    )

    assert "工具 [E:1070]。" in normalized
    assert len(citations) == 1


def test_definition_alignment_does_not_cite_unrelated_sentence() -> None:
    evidence = [{
        "chunk_id": 1070,
        "document_id": 9,
        "title": "Stateflow User Guide",
        "page": 1,
        "bbox": None,
        "heading_path": "Stateflow Overview",
        "content": "Stateflow models finite state machines.",
    }]

    normalized, _ = ensure_evidence_citations(
        "量子计算机可以预测天气。Stateflow 是状态机工具 [E:1070]。",
        evidence,
        require_sentence_citations=True,
    )

    assert "量子计算机可以预测天气 [E:" not in normalized


def test_definition_alignment_moves_post_period_citation_to_previous_sentence() -> None:
    evidence = [{
        "chunk_id": 1070,
        "document_id": 9,
        "title": "Stateflow User Guide",
        "page": 1,
        "bbox": None,
        "heading_path": "Stateflow Overview",
        "content": "MathWorks Stateflow models finite state machines (FSM) in Simulink.",
    }]

    normalized, _ = ensure_evidence_citations(
        "Stateflow 是 MathWorks 的 FSM 工具。[E:1070] 它集成在 Simulink 中 [E:1070]。",
        evidence,
        require_sentence_citations=True,
    )

    assert "工具 [E:1070]。" in normalized
    assert "。[E:1070]" not in normalized
