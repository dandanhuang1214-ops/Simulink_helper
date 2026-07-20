from app.services.coverage import (
    assess_evidence_coverage,
    assess_question_preconditions,
    insufficient_coverage_answer,
)


def test_private_failure_diagnosis_requires_runtime_artifacts() -> None:
    result = assess_evidence_coverage(
        "没有提供模型文件和运行日志时，你能直接判断我私有 Stateflow chart 为什么发生错误转移吗？",
        [{"content": "General Stateflow transition documentation."}],
    )
    assert result.passed is False
    assert result.reason == "missing_runtime_artifacts"
    answer = insufficient_coverage_answer(result)
    assert "无法直接判断" in answer
    assert "运行日志" in answer
    assert assess_question_preconditions(
        "没有提供模型文件和运行日志时，你能直接判断我私有 Stateflow chart 为什么发生错误转移吗？"
    ) is not None


def test_missing_logs_can_still_request_general_troubleshooting_guidance() -> None:
    result = assess_evidence_coverage(
        "没有运行日志时，Stateflow 错误转移的一般排查步骤是什么？",
        [{"content": "Stateflow errors transitions debugging steps"}],
    )
    assert result.reason != "missing_runtime_artifacts"
    assert assess_question_preconditions("没有运行日志时，一般排查步骤是什么？") is None


def test_diagnosis_with_artifacts_is_not_rejected_by_missing_artifact_rule() -> None:
    result = assess_evidence_coverage(
        "根据下面的模型文件和运行日志，能否判断错误转移的原因？",
        [{"content": "Stateflow model file runtime log error transition cause"}],
    )
    assert result.reason != "missing_runtime_artifacts"


def test_chinese_definition_requires_the_explicit_product_domain() -> None:
    relevant = assess_evidence_coverage(
        "Stateflow 是什么？",
        [{"title": "Stateflow User Guide", "content": "Stateflow charts model finite state machines."}],
    )
    unrelated = assess_evidence_coverage(
        "Stateflow 是什么？",
        [{"title": "Simulink Solver Guide", "content": "Choose a fixed-step solver."}],
    )

    assert relevant.passed is True
    assert relevant.required_terms == ["stateflow"]
    assert unrelated.passed is False
    assert unrelated.missing_terms == ["stateflow"]


def test_cross_domain_relationship_requires_both_products() -> None:
    result = assess_evidence_coverage(
        "Simulink 和 AUTOSAR 的关系是什么？",
        [{"title": "AUTOSAR Blockset", "content": "AUTOSAR component and runnable configuration."}],
    )

    assert result.passed is False
    assert result.required_terms == ["autosar", "simulink"]
    assert result.missing_terms == ["simulink"]


def test_english_solver_terms_are_covered_by_chinese_evidence() -> None:
    result = assess_evidence_coverage(
        "fixed-step solver 和 variable-step solver 有什么区别？",
        [
            {"heading_path": "固定步长求解器", "content": "固定步长适合确定执行周期。"},
            {"heading_path": "可变步长求解器", "content": "可变步长会根据误差动态调整。"},
        ],
    )

    assert result.passed is True
    assert result.missing_terms == []
