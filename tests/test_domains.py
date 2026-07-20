from app.services.domains import document_domains, preferred_domains


def test_domain_matching_does_not_match_ode_inside_model() -> None:
    domains = preferred_domains("createComponentAsModel")
    assert "solver" not in domains


def test_document_domain_matches_underscore_separated_product() -> None:
    domains = document_domains("MathWorks_AUTOSAR_Blockset_R2024a")
    assert "autosar" in domains


def test_multi_domain_query_remains_multi_label() -> None:
    domains = preferred_domains("Simulink 和 AUTOSAR 的关系")
    assert {"simulink", "autosar"}.issubset(domains)


def test_coverage_document_has_distinct_coverage_domain() -> None:
    domains = document_domains("MathWorks Simulink Coverage User Guide R2024a")
    assert {"simulink", "coverage"}.issubset(domains)
    assert "testing" not in domains


def test_coverage_query_does_not_force_simulink_test_domain() -> None:
    domains = preferred_domains("如何收集 Simulink 覆盖率数据并生成覆盖报告？")
    assert {"simulink", "coverage"}.issubset(domains)
    assert "testing" not in domains
