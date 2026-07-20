from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.domains import DOMAIN_REGISTRY, preferred_domains
from app.services.text import lexical_tokens
from app.services.question_aspects import evidence_aspect_coverage


GENERIC_TERMS = {
    "what", "how", "does", "can", "could", "should", "would", "is", "are", "the", "this", "that",
    "with", "from", "into", "directly", "relationship", "between", "use", "uses", "case", "cases",
    "generate", "generation", "model", "models", "system", "knowledge", "base", "support", "supports",
    "and", "or", "to", "in", "on", "of", "a", "an", "for", "by", "as",
    "matlab",
}

ACTION_TERMS = {
    "generate", "generation", "import", "export", "map", "maps", "mapping", "convert", "conversion",
    "simulate", "simulation", "support", "supports", "directly", "create", "creates", "open", "run",
    "measure", "measures", "measured", "measuring", "measurement",
}

DOMAIN_ONLY_TERMS = {
    keyword.lower()
    for spec in DOMAIN_REGISTRY.values()
    for keyword in spec.keywords
    if len(keyword) >= 3 and re.fullmatch(r"[a-z0-9_.:+-]+", keyword.lower())
}

# Some registry keywords name a narrow technical criterion rather than merely
# a broad product/domain. They must still be present in the retrieved evidence
# before generation; otherwise generic coverage documentation can incorrectly
# satisfy a question about that criterion.
EVIDENCE_REQUIRED_DOMAIN_TERMS = {
    "mcdc", "arxml", "runnable", "fixed-step", "variable-step", "mil", "sil", "pil",
}

MISSING_ARTIFACT_CUES = (
    "没有提供", "未提供", "缺少", "无法访问", "拿不到",
    "without", "not provided", "no model", "no code", "no log", "no trace",
)
RUNTIME_ARTIFACT_CUES = (
    "模型文件", "私有模型", "源代码", "代码", "运行日志", "日志", "执行轨迹", "转移轨迹",
    "输入", "事件", "复现步骤", "model file", "private model", "source code", "log", "trace",
    "runtime input", "event sequence", "reproduction",
)
DIRECT_DIAGNOSIS_CUES = (
    "直接判断", "直接确定", "定位根因", "判断原因", "确定原因", "为什么发生", "为什么报错",
    "determine why", "identify the root cause", "diagnose the cause", "what caused",
)

TERM_ALIASES: dict[str, set[str]] = {
    "ros2": {"ros2", "ros 2", "ros_2", "ros-2"},
    "ros": {"ros", "ros2", "ros 2"},
    "node": {"node", "nodes"},
    "nodes": {"node", "nodes"},
    "arxml": {"arxml", "xml"},
    "autosar": {"autosar"},
    "stateflow": {"stateflow"},
    "simulink": {"simulink"},
    "fixed-step": {"fixed-step", "fixed step", "fixedstep", "固定步长"},
    "variable-step": {"variable-step", "variable step", "variablestep", "可变步长"},
    "c/c++": {
        "c/c++", "c++", "c code", "c-code", "c/c++ code", "c and c++",
        "algorithmic c code", "code generation", "generated code",
    },
    "xml": {"xml", "arxml", "autosar xml", "autosar description"},
    "mcdc": {"mcdc", "mc/dc", "modified condition decision", "modified condition/decision"},
    "mil": {"mil", "model-in-the-loop", "model in the loop"},
    "sil": {"sil", "software-in-the-loop", "software in the loop"},
    "pil": {"pil", "processor-in-the-loop", "processor in the loop"},
    "simulink": {"simulink"},
    "stateflow": {"stateflow", "chart", "state machine"},
    "autosar": {"autosar", "arxml"},
    "solver": {"solver", "fixed-step", "fixed step", "variable-step", "variable step", "ode"},
    "testing": {"test", "testing", "verification", "mil", "sil", "pil", "hil"},
    "coverage": {"coverage", "mcdc", "decision coverage", "condition coverage", "cvdata"},
    "codegen": {"code generation", "generated code", "coder", "embedded coder", "c code"},
}


@dataclass(frozen=True)
class CoverageResult:
    passed: bool
    required_terms: list[str]
    covered_terms: list[str]
    missing_terms: list[str]
    coverage_ratio: float
    reason: str


def _normalize_term(value: str) -> str:
    value = value.lower().strip()
    if value in {"ros", "ros2", "ros_2", "ros-2"}:
        return "ros2" if value != "ros" else "ros"
    return value


def _question_terms(question: str) -> list[str]:
    # Domain labels are stable cross-language anchors.  For example, a Chinese
    # question containing “状态机” maps to ``stateflow`` and can still be
    # checked against an English manual.  Raw Chinese bigrams are deliberately
    # not treated as hard constraints: lexical_tokens() emits overlapping
    # bigrams, including boundary fragments such as “何收”, which caused both
    # false passes and false refusals.
    query_domains = preferred_domains(question)
    terms: list[str] = sorted(query_domains & {"simulink", "stateflow", "autosar"})
    tokens = [_normalize_term(item) for item in lexical_tokens(question)]
    for token in tokens:
        if re.search(r"[\u4e00-\u9fff]", token):
            continue
        if len(token) < 3:
            continue
        if token in GENERIC_TERMS:
            continue
        if token in DOMAIN_ONLY_TERMS and token not in EVIDENCE_REQUIRED_DOMAIN_TERMS:
            continue
        if token in ACTION_TERMS:
            continue
        if re.fullmatch(r"\d+", token):
            continue
        terms.append(token)
    # Category domains such as solver/testing/coverage are useful when the
    # question is Chinese-only, but should not dilute a more specific ASCII
    # anchor such as MCDC or runnable.
    if not terms:
        terms.extend(sorted(query_domains))
    return list(dict.fromkeys(terms))


def _evidence_text(evidence: list[dict]) -> str:
    values = []
    for item in evidence:
        values.append(str(item.get("title") or ""))
        values.append(str(item.get("heading_path") or ""))
        values.append(str(item.get("content") or ""))
    return "\n".join(values).lower()


def _term_covered(term: str, evidence_text: str, evidence_tokens: set[str]) -> bool:
    aliases = TERM_ALIASES.get(term, {term})
    for alias in aliases:
        alias = alias.lower()
        if " " in alias or "-" in alias or "_" in alias or re.search(r"[\u4e00-\u9fff]", alias):
            if alias in evidence_text:
                return True
        elif alias in evidence_tokens:
            return True
    return False


def _asks_for_diagnosis_without_runtime_artifacts(question: str) -> bool:
    lowered = question.casefold()
    return (
        any(cue in lowered for cue in MISSING_ARTIFACT_CUES)
        and any(cue in lowered for cue in RUNTIME_ARTIFACT_CUES)
        and any(cue in lowered for cue in DIRECT_DIAGNOSIS_CUES)
    )


def assess_question_preconditions(question: str) -> CoverageResult | None:
    """Reject constraints that retrieval cannot possibly repair."""
    if not _asks_for_diagnosis_without_runtime_artifacts(question):
        return None
    return CoverageResult(
        False,
        ["runtime_artifacts"],
        [],
        ["模型或代码、输入/事件、运行日志或执行轨迹"],
        0.0,
        "missing_runtime_artifacts",
    )


def assess_evidence_coverage(question: str, evidence: list[dict]) -> CoverageResult:
    """Lightweight pre-generation coverage gate.

    It is intentionally conservative only when the user asks for a specific
    out-of-KB technical constraint. General domain questions can proceed with
    domain evidence; questions containing uncovered specific terms should refuse
    before generation.
    """
    precondition = assess_question_preconditions(question)
    if precondition is not None:
        return precondition

    terms = _question_terms(question)
    covered_aspects, missing_aspects = evidence_aspect_coverage(question, evidence)
    if missing_aspects:
        return CoverageResult(
            False,
            [*terms, *[f"aspect:{item}" for item in covered_aspects + missing_aspects]],
            [*covered_aspects],
            [f"aspect:{item}" for item in missing_aspects],
            len(covered_aspects) / max(1, len(covered_aspects) + len(missing_aspects)),
            "missing_question_aspects",
        )
    if not terms:
        return CoverageResult(True, [], [], [], 1.0, "no_specific_terms")

    text = _evidence_text(evidence)
    evidence_tokens = set(lexical_tokens(text))
    covered = [term for term in terms if _term_covered(term, text, evidence_tokens)]
    missing = [term for term in terms if term not in covered]
    ratio = len(covered) / max(1, len(terms))

    query_domains = preferred_domains(question)
    # If the user mentions domain terms plus very specific uncovered terms, do
    # not let broad Simulink evidence answer a different question.
    if missing and len(terms) <= 3:
        return CoverageResult(False, terms, covered, missing, ratio, "specific_terms_missing")
    if missing and ratio < 0.5:
        return CoverageResult(False, terms, covered, missing, ratio, "low_specific_term_coverage")
    if not query_domains and missing:
        return CoverageResult(False, terms, covered, missing, ratio, "out_of_scope_terms_missing")
    return CoverageResult(True, terms, covered, missing, ratio, "covered_or_domain_supported")


def insufficient_coverage_answer(result: CoverageResult) -> str:
    if result.reason == "missing_runtime_artifacts":
        return (
            "缺少具体模型或代码、输入与事件、运行日志或执行轨迹时，无法直接判断私有系统的故障根因。"
            "请提供可复现的最小模型、复现步骤、诊断日志和相关状态/信号轨迹；在这些材料齐全前，"
            "我只能提供通用排查方法，不能指定某条状态转移或某段代码是根因。"
        )
    missing = "、".join(result.missing_terms) if result.missing_terms else "关键术语"
    return (
        "当前知识库中没有足够证据可靠回答这个问题。"
        f"检索到的资料没有覆盖问题中的关键约束：{missing}。"
        "你可以导入包含这些内容的官方文档，或换一个已入库资料覆盖的问题。"
    )
