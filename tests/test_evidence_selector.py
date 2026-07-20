from app.services.evidence_selector import _requested_procedure_stages, question_role, question_roles, select_evidence


def test_procedure_noun_question_is_not_forced_to_definition_procedure() -> None:
    assert question_role("创建模型并运行仿真的基本流程是什么？", {"simulink"}) == "procedure"
    assert question_role("What are the steps to run a simulation?", {"simulink"}) == "procedure"
    assert question_role("请说明定位子系统并追踪信号的操作顺序", {"simulink"}) == "procedure"


def test_indirect_multi_domain_mapping_is_relationship() -> None:
    assert question_role(
        "AUTOSAR 端口和 runnable 到了 Simulink 模型分别落在什么位置？",
        {"autosar", "simulink"},
    ) == "relationship"


def test_cross_domain_how_question_keeps_relationship_and_procedure_roles() -> None:
    roles = question_roles(
        "Stateflow chart 如何与 Simulink 信号交互？",
        {"stateflow", "simulink"},
    )

    assert roles == {"relationship", "procedure"}
    assert question_role(
        "Stateflow chart 如何与 Simulink 信号交互？",
        {"stateflow", "simulink"},
    ) == "relationship"


def test_workflow_requests_stage_diversity_but_single_action_does_not() -> None:
    assert _requested_procedure_stages("创建模型并运行仿真的基本流程是什么？") == [
        "setup", "build", "configure", "execute", "inspect",
    ]
    assert _requested_procedure_stages("如何把测试步骤链接到需求？") == []
    assert _requested_procedure_stages("如何收集覆盖率数据并生成报告？") == ["execute", "report"]
from app.services.evidence_snippets import _snippet_for_item, answer_generation_budget, select_prompt_evidence
from app.services.coverage import assess_evidence_coverage


def _candidate(
    chunk_id: int,
    heading: str,
    content: str,
    *,
    document_id: int = 11,
    domains: list[str] | None = None,
    score: float = 0.08,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "title": "MathWorks Simulink Coverage User Guide",
        "document_domains": domains or ["simulink", "testing"],
        "content": content,
        "ordinal": chunk_id,
        "page": chunk_id,
        "heading_path": heading,
        "rrf_score": score,
        "final_score": score,
        "channels": ["bm25", "dense"],
    }


def test_mixed_question_is_not_reduced_to_procedure() -> None:
    assert question_role("What is MCDC coverage and how is it measured?") == "definition_procedure"


def test_mixed_question_keeps_definition_and_procedure_evidence() -> None:
    candidates = [
        _candidate(
            1,
            "Signal Size Coverage Report",
            "Click Generate Report and select the result in the coverage explorer.",
            score=0.11,
        ),
        _candidate(
            2,
            "MCDC Definitions / Unique-Cause MCDC",
            "MCDC is a coverage criterion in which each condition independently affects the decision outcome.",
            score=0.07,
        ),
        _candidate(
            3,
            "Measure MCDC Coverage",
            "Run the simulation, collect MCDC coverage, and inspect the measured decision outcomes.",
            score=0.09,
        ),
        _candidate(
            4,
            "Generate Test Cases",
            "Create a test with Simulink Design Verifier and export it to Simulink Test.",
            document_id=10,
            score=0.10,
        ),
    ]

    selected = select_evidence(
        "What is MCDC coverage and how is it measured?",
        candidates,
        final_limit=4,
    )
    selected_ids = {item["chunk_id"] for item in selected}
    assert 2 in selected_ids
    assert 3 in selected_ids

    prompt_items, role = select_prompt_evidence(
        "What is MCDC coverage and how is it measured?",
        selected,
    )
    assert role == "definition_procedure"
    assert 2 in {item["chunk_id"] for item in prompt_items}
    assert 3 in {item["chunk_id"] for item in prompt_items}


def test_definition_of_wrong_concept_does_not_displace_query_topic() -> None:
    candidates = [
        _candidate(
            10,
            "What Is a Coverage Filter?",
            "A coverage filter is a set of rules used to exclude model objects.",
            score=0.12,
        ),
        _candidate(
            11,
            "MCDC Definitions",
            "MCDC is a criterion showing that each condition independently affects a decision.",
            score=0.07,
        ),
        _candidate(
            12,
            "Analyze MCDC",
            "Run MCDC coverage and inspect each condition and decision outcome.",
            score=0.08,
        ),
    ]
    selected = select_evidence(
        "What is MCDC coverage and how is it measured?",
        candidates,
        final_limit=2,
    )
    assert {item["chunk_id"] for item in selected} == {11, 12}


def test_measurement_verb_is_not_treated_as_missing_technical_constraint() -> None:
    evidence = [_candidate(
        21,
        "MCDC Definitions",
        "MCDC records whether each condition independently affects a decision outcome.",
    )]
    result = assess_evidence_coverage(
        "What is MCDC coverage and how is it measured?",
        evidence,
    )
    assert result.passed is True
    assert result.required_terms == ["mcdc"]


def test_cross_language_manual_snippet_keeps_heading_relevant_action() -> None:
    item = _candidate(
        30,
        "Link Test Steps to Requirements",
        (
            "To link a test step to a requirement, open the Test Sequence block and select the step. "
            "Use the requirements pane to create the link and save the model. "
            "The link is then available for traceability reports."
        ),
    )
    snippet = _snippet_for_item("如何把测试步骤链接到需求？", item, max_chars=150)
    assert "requirements" in snippet.casefold()
    assert "link" in snippet.casefold()


def test_cross_language_snippet_can_use_retrieval_query_proxy() -> None:
    item = _candidate(
        31,
        "Generate Coverage Results for Models",
        (
            "You can inspect existing settings in the application. "
            "Enable coverage analysis and run the model to collect coverage data. "
            "Generate a model coverage report after the simulation completes."
        ),
    )
    snippet = _snippet_for_item(
        "如何收集覆盖率数据并生成报告？",
        item,
        max_chars=170,
        proxy_queries=["collect coverage data generate model coverage report"],
    )
    assert "collect coverage data" in snippet.casefold()
    assert "coverage report" in snippet.casefold()


def test_focused_single_action_limits_prompt_evidence_to_three() -> None:
    evidence = [
        _candidate(
            40 + index,
            "Link Test Steps to Requirements",
            f"Procedure evidence block {index}: select the test step and create a requirement link.",
            score=0.20 - index * 0.01,
        )
        for index in range(5)
    ]
    prompt_items, role = select_prompt_evidence("如何把测试步骤链接到需求？", evidence)
    assert role == "procedure"
    assert len(prompt_items) == 3


def test_definition_uses_two_evidence_items_and_small_generation_budget() -> None:
    evidence = [
        _candidate(
            45 + index,
            "Stateflow Overview",
            f"Stateflow definition evidence {index} for finite state machine modeling.",
            domains=["stateflow"],
        )
        for index in range(4)
    ]

    prompt_items, role = select_prompt_evidence("Stateflow 是什么？", evidence)

    assert role == "definition"
    assert len(prompt_items) == 2
    assert answer_generation_budget("Stateflow 是什么？") == 110


def test_generic_model_coverage_prefers_model_coverage_over_sil_code_coverage() -> None:
    candidates = [
        _candidate(
            50,
            "Collect Model Coverage",
            "Run the model simulation and collect model coverage results in the coverage explorer.",
            score=0.08,
        ),
        _candidate(
            51,
            "Software-in-the-Loop Code Coverage",
            "Run software-in-the-loop simulation to collect generated code coverage.",
            score=0.10,
        ),
    ]
    selected = select_evidence("如何收集并查看 Simulink 模型覆盖率？", candidates, final_limit=1)
    assert selected[0]["chunk_id"] == 50


def test_procedure_stage_prefers_direct_retrieval_over_wiki_only_evidence() -> None:
    run_item = _candidate(
        60,
        "Generate Coverage Results / Run Simulation",
        "Run the model to collect coverage results.",
        score=0.09,
    )
    wiki_report = _candidate(
        61,
        "Top-Level Model Coverage Report / Specialized Signal Report",
        "This specialized report contains signal details.",
        score=0.14,
    )
    wiki_report["channels"] = ["wiki"]
    direct_report = _candidate(
        62,
        "Top-Level Model Coverage Report / Aggregated Tests",
        "Generate a model coverage report after collecting results.",
        score=0.08,
    )
    direct_report["channels"] = ["bm25"]

    selected = select_evidence(
        "如何收集覆盖率数据并生成报告？",
        [wiki_report, direct_report, run_item],
        final_limit=2,
    )
    assert {item["chunk_id"] for item in selected} == {60, 62}


def test_explicit_two_stage_request_is_not_padded_with_unrelated_evidence() -> None:
    candidates = [
        _candidate(70, "Generate Coverage Results / Run Simulation", "Run the model and collect coverage.", score=0.12),
        _candidate(71, "Top-Level Model Coverage Report", "Generate the model coverage report.", score=0.11),
        _candidate(72, "Coverage Filters", "Edit filters for a specialized report.", score=0.10),
        _candidate(73, "Variant Coverage", "Configure inactive variant filtering.", score=0.09),
    ]
    selected = select_evidence(
        "如何收集覆盖率数据并生成报告？",
        candidates,
        final_limit=4,
    )
    assert {item["chunk_id"] for item in selected} == {70, 71}


def test_cross_domain_direct_hit_does_not_displace_exact_domain_workflow_stage() -> None:
    run_item = _candidate(
        80,
        "Create a Simple Model / Run Simulation",
        "Run the Simulink model to execute the configured simulation.",
        domains=["simulink"],
    )
    exact_view = _candidate(
        81,
        "Create a Simple Model / View Simulation Data",
        "View simulation results in the data inspector.",
        domains=["simulink"],
    )
    exact_view["channels"] = ["wiki"]
    cross_domain_view = _candidate(
        82,
        "Coverage Results / View Coverage Results",
        "View model coverage results.",
        domains=["simulink", "coverage"],
        score=0.20,
    )
    cross_domain_view["channels"] = ["bm25"]
    selected = select_evidence(
        "如何运行 Simulink 仿真并查看结果？",
        [cross_domain_view, run_item, exact_view],
        final_limit=5,
    )
    selected_ids = {item["chunk_id"] for item in selected}
    assert 81 in selected_ids
    assert 82 not in selected_ids


def test_compound_question_reserves_one_item_per_engineering_aspect() -> None:
    candidates = [
        _candidate(90, "Import AUTOSAR Composition", "Import an AUTOSAR composition from ARXML.", score=0.20),
        _candidate(91, "Model AUTOSAR Software Components", "A Simulink representation implements AUTOSAR software component behavior.", score=0.10),
        _candidate(92, "AUTOSAR Code Mappings", "Map Simulink elements to AUTOSAR ports, runnable entities, and IRV data.", score=0.09),
        _candidate(93, "Generate AUTOSAR C Code and XML Descriptions", "Generate C code and export ARXML descriptions with Embedded Coder.", score=0.08),
    ]
    selected = select_evidence(
        "Simulink 与 AUTOSAR 在组件建模、接口映射和代码生成流程中是什么关系？",
        candidates,
        final_limit=3,
    )

    assert {item["chunk_id"] for item in selected} == {91, 92, 93}


def test_harness_definition_sync_and_management_are_all_covered() -> None:
    candidates = [
        _candidate(100, "Import Test Harness", "Import a standalone model as a test harness.", score=0.20),
        _candidate(101, "Test Harness and Model Relationship", "A test harness is an isolated environment around the component under test.", score=0.09),
        _candidate(102, "Synchronize Changes Between Test Harness and Model", "Use Push Changes from harness to model and Rebuild Harness from model to harness.", score=0.08),
        _candidate(103, "Manage Test Harnesses", "A harness can be saved internally or externally and keeps its model association metadata.", score=0.07),
    ]
    question = "Simulink Test harness 是什么，它与被测模型之间如何同步和管理？"
    selected = select_evidence(question, candidates, final_limit=3)
    result = assess_evidence_coverage(question, selected)

    assert {item["chunk_id"] for item in selected} == {101, 102, 103}
    assert result.passed is True
    assert answer_generation_budget(question) == 420
