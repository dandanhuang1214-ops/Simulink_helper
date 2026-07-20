from app.services.retrieval import (
    _dense_skip_reason,
    _domain_expanded_queries,
    _fts_query,
    _expansion_heading_boost,
    _is_graph_led_channels,
    _is_procedural_query,
    _should_use_graph,
    _should_use_wiki,
    _weighted_query_plan,
)


def test_fts_query_preserves_technical_terms() -> None:
    query = _fts_query("R2025a 中 find_system 怎么使用？")

    assert '"r2025a"' in query
    assert '"find_system"' in query
    assert '"怎么"' in query


def test_mcdc_query_expands_acronym_to_definition_terms() -> None:
    queries = _domain_expanded_queries("What is MCDC and how is it measured?")
    expanded = " ".join(queries).lower()
    assert "modified condition decision" in expanded
    assert "independence" in expanded


def test_model_creation_workflow_expands_to_manual_section_terms() -> None:
    queries = _domain_expanded_queries("从空白模型开始创建 Simulink 模型并运行仿真")
    expanded = " ".join(queries).lower()

    assert all(term in expanded for term in ("create", "simple", "model"))
    assert all(term in expanded for term in ("view", "simulation", "results"))


def test_coverage_collection_expands_to_results_and_report_terms() -> None:
    queries = _domain_expanded_queries("如何收集 Simulink 覆盖率数据并生成覆盖报告？")
    expanded = " ".join(queries).lower()

    assert all(term in expanded for term in ("generate", "coverage", "results", "models"))
    assert all(term in expanded for term in ("model", "coverage", "report", "cvhtml", "cvdata"))
    assert all(len(_fts_query(query).split(" OR ")) <= 12 for query in queries)
    assert all(term in _fts_query(queries[1]) for term in ("model", "report", "cvhtml", "cvdata"))


def test_product_names_do_not_masquerade_as_exact_api_identifiers() -> None:
    scores = {index: 1.0 for index in range(12)}

    reason = _dense_skip_reason(
        "Stateflow chart 如何与 Simulink 模型中的信号和仿真执行相互作用？",
        scores,
        20,
    )

    assert reason != "skip:dense_fast_path_exact_identifier"


def test_camel_case_api_can_use_exact_identifier_fast_path() -> None:
    scores = {index: 1.0 for index in range(12)}

    reason = _dense_skip_reason("createCompositionAsModel 如何导入 ARXML？", scores, 20)

    assert reason == "skip:dense_fast_path_exact_identifier"


def test_graph_plus_wiki_without_direct_retrieval_is_still_graph_led() -> None:
    assert _is_graph_led_channels({"graph"})
    assert _is_graph_led_channels({"graph", "wiki"})
    assert not _is_graph_led_channels({"graph", "bm25"})
    assert not _is_graph_led_channels({"graph", "dense", "wiki"})


def test_expansion_terms_boost_matching_manual_heading() -> None:
    queries = _domain_expanded_queries("如何收集 Simulink 覆盖率数据并生成覆盖报告？")
    matching = {"title": "Simulink Coverage", "heading_path": "Generate Coverage Results for Models"}
    unrelated = {"title": "Simulink Coverage", "heading_path": "Coverage Filter Rules"}

    assert _expansion_heading_boost(queries, matching) > _expansion_heading_boost(queries, unrelated)


def test_procedural_query_gets_deeper_lexical_pool() -> None:
    assert _is_procedural_query("如何收集覆盖率数据并生成报告？")
    assert _is_procedural_query("请说明定位子系统并追踪信号的操作顺序")
    assert not _is_procedural_query("MCDC 是什么？")


def test_weighted_query_plan_keeps_original_strongest_and_bounded() -> None:
    query = "AUTOSAR 组件的 runnable 和 Simulink 端口分别怎样映射？"
    plan = _weighted_query_plan(query, [query])

    assert plan[0].text == query
    assert plan[0].weight == 1.0
    assert all(item.weight < plan[0].weight for item in plan[1:])
    assert len(plan) <= 5
    blended = [item for item in plan if item.source == "blended_facet"]
    assert len(blended) == 1
    assert all(term in blended[0].text.casefold() for term in ("autosar", "runnable", "simulink"))


def test_weighted_query_plan_does_not_duplicate_rewrite() -> None:
    query = "怎样更新 ARXML？"
    plan = _weighted_query_plan(query, [query, query, "update AUTOSAR ARXML model"])

    assert sum(item.source == "rewrite" for item in plan) == 1


def test_compound_autosar_question_gets_bounded_aspect_facets() -> None:
    query = "Simulink 与 AUTOSAR 在组件建模、接口映射和代码生成流程中是什么关系？"
    plan = _weighted_query_plan(query, [query])
    facets = [item.text.casefold() for item in plan if item.source == "aspect_facet"]

    assert len(plan) <= 5
    assert any("component behavior" in item for item in facets)
    assert any("ports runnable irv" in item for item in facets)
    assert any("arxml" in item and "c code" in item for item in facets)


def test_wiki_and_graph_are_routed_by_question_role() -> None:
    assert _should_use_wiki("Stateflow 是什么？", {"stateflow"}) == (True, "concept_or_relationship")
    assert _should_use_graph("Stateflow 是什么？", {"stateflow"}) == (
        False,
        "simple_query_no_graph_expansion",
    )
    assert _should_use_wiki("如何运行 Simulink 仿真？", {"simulink"}) == (
        False,
        "focused_procedure_prefers_raw_evidence",
    )
    assert _should_use_graph("Simulink 和 AUTOSAR 的关系", {"simulink", "autosar"}) == (
        True,
        "relationship_or_comparison",
    )
    assert _should_use_graph("Stateflow 状态如何影响同一 chart 中的转移？", {"stateflow"}) == (
        True,
        "explicit_multi_hop_cue",
    )
