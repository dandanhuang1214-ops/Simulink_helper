from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainSpec:
    keywords: tuple[str, ...]
    title_keywords: tuple[str, ...]


DOMAIN_REGISTRY: dict[str, DomainSpec] = {
    "simulink": DomainSpec(
        keywords=(
            "simulink", "model", "block", "signal", "subsystem", "simulation",
            "仿真", "模型", "模块", "信号", "子系统",
        ),
        title_keywords=("simulink",),
    ),
    "stateflow": DomainSpec(
        keywords=(
            "stateflow", "chart", "state", "transition", "event", "truth table",
            "flow chart", "状态机", "状态图", "状态", "转移", "转换", "事件", "真值表",
        ),
        title_keywords=("stateflow",),
    ),
    "autosar": DomainSpec(
        keywords=(
            "autosar", "arxml", "composition", "component", "runnable", "rte",
            "port", "software component", "软件组件", "组合", "端口",
        ),
        title_keywords=("autosar", "arxml"),
    ),
    "solver": DomainSpec(
        keywords=(
            "solver", "fixed-step", "variable-step", "step size", "ode",
            "求解器", "固定步长", "可变步长", "步长",
        ),
        title_keywords=("solver", "求解器"),
    ),
    "testing": DomainSpec(
        keywords=(
            "mil", "sil", "pil", "hil", "test", "verify", "verification",
            "coverage", "requirements", "测试", "验证", "覆盖率", "需求",
        ),
        title_keywords=("mil", "sil", "pil", "test", "verify", "测试", "验证"),
    ),
    "codegen": DomainSpec(
        keywords=(
            "code generation", "coder", "embedded coder", "c code", "生成代码",
            "代码生成", "嵌入式", "embedded",
        ),
        title_keywords=("coder", "code generation", "embedded"),
    ),
}


def normalize_text(value: str) -> str:
    return value.lower()


def _keyword_in_text(keyword: str, lowered: str) -> bool:
    normalized = keyword.lower()
    if re.fullmatch(r"[a-z0-9_./:+ -]+", normalized):
        # ASCII technical terms must match token boundaries. Plain substring
        # matching incorrectly classifies "model" as the solver term "ode".
        return bool(re.search(
            rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
            lowered,
        ))
    return normalized in lowered


def preferred_domains(query: str) -> set[str]:
    """Return all knowledge domains mentioned by the user query.

    This is intentionally multi-label. A query like "Simulink 和 AUTOSAR 的关系"
    should prefer both domains rather than being forced into a single class.
    """
    lowered = normalize_text(query)
    domains: set[str] = set()
    for domain, spec in DOMAIN_REGISTRY.items():
        if any(_keyword_in_text(keyword, lowered) for keyword in spec.keywords):
            domains.add(domain)
    return domains


def document_domains(title: str) -> set[str]:
    lowered = normalize_text(title)
    domains: set[str] = set()
    for domain, spec in DOMAIN_REGISTRY.items():
        if any(_keyword_in_text(keyword, lowered) for keyword in spec.title_keywords):
            domains.add(domain)
    return domains


def domain_boost(query_domains: set[str], doc_domains: set[str]) -> float:
    if not query_domains or not doc_domains:
        return 0.0
    overlap = query_domains & doc_domains
    if not overlap:
        return 0.0
    # Small enough to avoid hard filtering, large enough to resolve noisy ties.
    return min(0.08, 0.04 * len(overlap))
