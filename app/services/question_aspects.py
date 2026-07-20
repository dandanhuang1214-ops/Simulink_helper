from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionAspect:
    name: str
    triggers: tuple[str, ...]
    facet: str
    evidence_terms: tuple[str, ...]
    context: tuple[str, ...] = ()
    answer_requirement: str = ""


# This is a small domain ontology, not a list of benchmark questions.  It maps
# stable engineering concepts to the English vocabulary used by the manuals.
# New domains can extend the registry without adding branches to the router.
ASPECT_REGISTRY = (
    QuestionAspect(
        "component_modeling",
        ("组件建模", "component modeling", "model component", "component behavior"),
        "AUTOSAR software component Simulink model component behavior implementation",
        ("software component", "model component", "component behavior", "simulink representation"),
        answer_requirement="说明 Simulink 如何承载 AUTOSAR 软件组件行为",
    ),
    QuestionAspect(
        "interface_mapping",
        ("接口映射", "端口映射", "interface mapping", "port mapping", "code mapping"),
        "AUTOSAR code mappings Simulink elements ports runnable IRV interface mapping",
        ("mapping", "mapped", "port", "interface", "inter-runnable", "irv"),
        answer_requirement="说明 Simulink 元素如何映射到 AUTOSAR 端口、runnable 或 IRV",
    ),
    QuestionAspect(
        "code_generation",
        ("代码生成", "生成代码", "code generation", "generate code"),
        "Generate AUTOSAR C Code XML ARXML descriptions Embedded Coder",
        ("generate code", "generated code", "c code", "arxml", "xml descriptions", "embedded coder"),
        answer_requirement="说明代码生成与 ARXML 输出及其工具前提",
    ),
    QuestionAspect(
        "runnable_configuration",
        ("runnable", "可运行实体"),
        "AUTOSAR runnable executable entity entry-point function mapping configure RTE event",
        ("runnable", "entry-point function", "rte", "event", "schedulable entity"),
        answer_requirement="说明 runnable 的定义、入口函数映射与事件配置",
    ),
    QuestionAspect(
        "synchronization",
        ("同步", "synchronization", "synchronize", "rebuild", "push changes"),
        "Synchronize Changes Between Test Harness and Model push rebuild synchronization mode",
        ("synchronize", "synchronization", "push changes", "rebuild harness", "model to the harness", "harness to the model"),
        answer_requirement="说明 model 到 harness 的 rebuild 与 harness 到 model 的 push/同步方向",
    ),
    QuestionAspect(
        "harness_definition",
        ("是什么", "what is", "define", "definition"),
        "Test Harness and Model Relationship isolated environment component under test",
        ("test harness is", "isolated environment", "component under test", "harness-model relationship"),
        ("harness", "测试框架", "测试线束"),
        "明确说明 Test Harness 为被测组件提供隔离的测试环境",
    ),
    QuestionAspect(
        "harness_management",
        ("管理", "manage", "management", "内部", "外部", "internal", "external"),
        "Manage Test Harnesses saved internally externally association metadata",
        (
            "saved internally", "saved externally", "internally", "externally",
            "saveexternally", "internal harness", "external harness",
        ),
        ("harness", "测试框架", "测试线束"),
        "明确说明 Test Harness 可以内部或外部保存",
    ),
)


def requested_aspects(question: str) -> list[QuestionAspect]:
    lowered = question.casefold()
    result: list[QuestionAspect] = []
    for aspect in ASPECT_REGISTRY:
        if aspect.context and not any(term in lowered for term in aspect.context):
            continue
        if any(trigger in lowered for trigger in aspect.triggers):
            result.append(aspect)
    return result


def aspect_query_facets(question: str) -> list[str]:
    return [aspect.facet for aspect in requested_aspects(question)]


def aspect_evidence_score(aspect: QuestionAspect, item: dict) -> float:
    heading = f"{item.get('title') or ''} {item.get('heading_path') or ''}".casefold()
    content = (item.get("content") or "").casefold()
    heading_hits = sum(term in heading for term in aspect.evidence_terms)
    content_hits = sum(term in content[:2400] for term in aspect.evidence_terms)
    # A heading match is more discriminative than a passing mention in a long
    # manual page. Scores are bounded and only used for deterministic coverage.
    return min(1.0, heading_hits * 0.34 + content_hits * 0.14)


def evidence_aspect_coverage(question: str, evidence: list[dict]) -> tuple[list[str], list[str]]:
    covered: list[str] = []
    missing: list[str] = []
    for aspect in requested_aspects(question):
        best = max((aspect_evidence_score(aspect, item) for item in evidence), default=0.0)
        (covered if best >= 0.28 else missing).append(aspect.name)
    return covered, missing
