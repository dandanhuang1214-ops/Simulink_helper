from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.domains import DOMAIN_REGISTRY, preferred_domains
from app.services.text import lexical_tokens


GENERIC_TERMS = {
    "what", "how", "does", "can", "could", "should", "would", "is", "are", "the", "this", "that",
    "with", "from", "into", "directly", "relationship", "between", "use", "uses", "case", "cases",
    "generate", "generation", "model", "models", "system", "knowledge", "base", "support", "supports",
    "and", "or", "to", "in", "on", "of", "a", "an", "for", "by", "as",
    "simulink", "matlab",
}

ACTION_TERMS = {
    "generate", "generation", "import", "export", "map", "maps", "mapping", "convert", "conversion",
    "simulate", "simulation", "support", "supports", "directly", "create", "creates", "open", "run",
}

DOMAIN_ONLY_TERMS = {
    keyword.lower()
    for spec in DOMAIN_REGISTRY.values()
    for keyword in spec.keywords
    if len(keyword) >= 3 and re.fullmatch(r"[a-z0-9_.:+-]+", keyword.lower())
}

TERM_ALIASES: dict[str, set[str]] = {
    "ros2": {"ros2", "ros 2", "ros_2", "ros-2"},
    "ros": {"ros", "ros2", "ros 2"},
    "node": {"node", "nodes"},
    "nodes": {"node", "nodes"},
    "arxml": {"arxml", "xml"},
    "autosar": {"autosar"},
    "stateflow": {"stateflow"},
    "simulink": {"simulink"},
    "fixed-step": {"fixed-step", "fixed step", "fixedstep"},
    "variable-step": {"variable-step", "variable step", "variablestep"},
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
    tokens = [_normalize_term(item) for item in lexical_tokens(question)]
    terms: list[str] = []
    for token in tokens:
        if len(token) < 3:
            continue
        if token in GENERIC_TERMS:
            continue
        if token in DOMAIN_ONLY_TERMS:
            continue
        if token in ACTION_TERMS:
            continue
        if re.fullmatch(r"\d+", token):
            continue
        terms.append(token)
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
        if " " in alias or "-" in alias or "_" in alias:
            if alias in evidence_text:
                return True
        elif alias in evidence_tokens:
            return True
    return False


def assess_evidence_coverage(question: str, evidence: list[dict]) -> CoverageResult:
    """Lightweight pre-generation coverage gate.

    It is intentionally conservative only when the user asks for a specific
    out-of-KB technical constraint. General domain questions can proceed with
    domain evidence; questions containing uncovered specific terms should refuse
    before generation.
    """
    terms = _question_terms(question)
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
    missing = "、".join(result.missing_terms) if result.missing_terms else "关键术语"
    return (
        "当前知识库中没有足够证据可靠回答这个问题。"
        f"检索到的资料没有覆盖问题中的关键约束：{missing}。"
        "你可以导入包含这些内容的官方文档，或换一个已入库资料覆盖的问题。"
    )
