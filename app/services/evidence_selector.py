from __future__ import annotations

import re
from collections import defaultdict

from app.services.domains import preferred_domains
from app.services.question_aspects import aspect_evidence_score, requested_aspects
from app.services.text import lexical_tokens


RELATION_QUERY_CUES = (
    "relationship", "relation", "relate", "connect", "integration", "integrate", "mapping", "map",
    "import", "export", "interface", "interact", "between",
    "关系", "关联", "联系", "连接", "集成", "映射", "导入", "导出", "接口", "交互", "之间",
    "对应", "落在", "分别", "各自",
)
COMPARISON_QUERY_CUES = ("compare", "difference", "versus", "vs", "区别", "对比", "比较", "差异")
PROCEDURE_QUERY_CUES = (
    "how", "steps", "workflow", "process", "怎么", "怎样", "如何", "步骤", "流程",
    "操作顺序", "接下来", "先后",
)
DEFINITION_QUERY_CUES = ("what is", "define", "definition", "是什么", "定义")

DEFINITION_EVIDENCE_CUES = (
    "definition", "definitions", "defined as", "means", "refers to", "is a", "is an",
    "what is", "overview", "concept", "metric", "criterion", "criteria",
    "定义", "是指", "含义", "概念", "指标", "准则",
)

STRONG_DEFINITION_PATTERNS = (
    "tests the independence", "independently affects", "independent of any other",
    "achieves full coverage", "is represented by", "is calculated as", "is defined as",
    "独立影响", "完整覆盖", "计算公式", "定义为",
)

GENERIC_TOPIC_TERMS = {
    "what", "which", "how", "does", "do", "did", "is", "are", "was", "were",
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "by",
    "define", "defined", "definition", "measure", "measured", "measurement", "using",
}

RELATION_EVIDENCE_CUES = (
    "relationship", "related", "relate", "connect", "connection", "integration", "integrate",
    "mapping", "mapped", "map", "import", "export", "interface", "interact", "composition",
    "component", "arxml", "runnable", "port", "signal", "subsystem", "model reference",
    "关系", "关联", "联系", "连接", "集成", "映射", "导入", "导出", "接口", "交互", "组成", "组件", "端口", "信号",
)
PROCEDURE_EVIDENCE_CUES = (
    "how to", "steps", "workflow", "process", "procedure", "import", "export", "configure",
    "create", "update", "generate", "link", "open", "select", "click", "call",
    "createcomponentasmodel", "createcompositionasmodel", "updateautosarproperties", "updatemodel",
    "导入", "导出", "配置", "创建", "更新", "生成", "链接", "打开", "选择", "调用", "步骤", "流程",
)

PROCEDURE_STAGE_HEADING_CUES = {
    "setup": ("open", "create", "blank", "import", "new model", "打开", "创建", "导入"),
    "build": ("add block", "connect block", "construction", "添加", "连接", "构建"),
    "configure": ("configure", "mapping", "map ", "edit", "parameter", "property", "配置", "映射", "参数", "属性"),
    "execute": ("run", "simulate", "collect", "execute", "generate coverage", "运行", "仿真", "收集", "执行"),
    "report": ("report", "reporting", "html", "cvhtml", "报告", "导出"),
    "inspect": ("view", "result", "analyze", "analysis", "explorer", "查看", "结果", "分析"),
}


def question_roles(question: str, query_domains: set[str] | None = None) -> set[str]:
    """Return every observable intent instead of forcing one routing label."""
    lowered = question.lower()
    domains = query_domains if query_domains is not None else preferred_domains(question)
    roles: set[str] = set()
    if any(cue in lowered for cue in COMPARISON_QUERY_CUES):
        roles.add("comparison")
    has_procedure = any(cue in lowered for cue in PROCEDURE_QUERY_CUES)
    has_definition = any(cue in lowered for cue in DEFINITION_QUERY_CUES)
    has_relationship = len(domains) >= 2 and any(cue in lowered for cue in RELATION_QUERY_CUES)
    if has_relationship:
        roles.add("relationship")
    if has_definition and has_procedure:
        # “流程/步骤是什么” asks for an ordered procedure, not for both a
        # concept definition and a procedure. Treating the sentence-final
        # “是什么” as a separate definition intent forces unrelated overview
        # chunks into the final evidence set.
        procedure_noun_question = bool(re.search(
            r"(?:流程|步骤|过程).{0,8}是什么|what\s+(?:is|are)\s+the\s+(?:process|steps|workflow)",
            lowered,
        ))
        if procedure_noun_question:
            roles.add("procedure")
        else:
            roles.update({"definition", "procedure"})
    else:
        if has_procedure:
            roles.add("procedure")
        if has_definition:
            roles.add("definition")
    return roles or {"general"}


def question_role(question: str, query_domains: set[str] | None = None) -> str:
    """Compatibility primary role; routing decisions should use question_roles."""
    roles = question_roles(question, query_domains)
    if "comparison" in roles:
        return "comparison"
    if {"definition", "procedure"}.issubset(roles):
        return "definition_procedure"
    # Cross-domain relationship is more discriminative than a generic “如何”.
    if "relationship" in roles:
        return "relationship"
    if "procedure" in roles:
        return "procedure"
    if "definition" in roles:
        return "definition"
    return "general"


def _relation_evidence_score(item: dict) -> float:
    text = f"{item.get('title') or ''} {item.get('heading_path') or ''} {item.get('content') or ''}".lower()
    hits = sum(1 for cue in RELATION_EVIDENCE_CUES if cue in text)
    return min(0.045, hits * 0.009)


def _procedure_evidence_score(item: dict) -> float:
    text = f"{item.get('title') or ''} {item.get('heading_path') or ''} {item.get('content') or ''}".lower()
    hits = sum(1 for cue in PROCEDURE_EVIDENCE_CUES if cue in text)
    return min(0.050, hits * 0.010)


def _procedure_stage(item: dict) -> str | None:
    heading_path = (item.get("heading_path") or "").casefold()
    leaf_heading = heading_path.rsplit("/", 1)[-1].strip()
    full_heading = f"{item.get('title') or ''} {heading_path}".casefold()
    for heading in (leaf_heading, full_heading):
        for stage, cues in PROCEDURE_STAGE_HEADING_CUES.items():
            if any(cue in heading for cue in cues):
                return stage
    return None


def _requested_procedure_stages(question: str) -> list[str]:
    lowered = question.casefold()
    workflow_request = (
        any(cue in lowered for cue in ("流程", "workflow", "steps", "process"))
        or bool(re.search(r"(?:操作|具体|基本|主要|完整|实现)?步骤.{0,6}(?:是|有|包括|哪些|什么|：|:)", lowered))
        or bool(re.search(r"(?:有|包括|需要)哪些.{0,4}步骤", lowered))
    )
    if workflow_request:
        return ["setup", "build", "configure", "execute", "inspect"]
    requested: list[str] = []
    cue_groups = {
        "setup": ("创建", "打开", "导入", "create", "open", "import"),
        "configure": ("配置", "映射", "参数", "configure", "mapping", "map"),
        "execute": ("运行", "仿真", "收集", "执行", "run", "simulate", "collect", "execute"),
        "report": ("报告", "导出", "report", "export", "html"),
        "inspect": ("查看", "结果", "分析", "view", "result", "analyze"),
    }
    for stage, cues in cue_groups.items():
        if any(cue in lowered for cue in cues):
            requested.append(stage)
    return requested


def _definition_evidence_score(item: dict) -> float:
    heading = f"{item.get('title') or ''} {item.get('heading_path') or ''}".lower()
    content = (item.get("content") or "").lower()
    heading_hits = sum(1 for cue in DEFINITION_EVIDENCE_CUES if cue in heading)
    content_hits = sum(1 for cue in DEFINITION_EVIDENCE_CUES if cue in content[:1400])
    strong_hits = sum(1 for cue in STRONG_DEFINITION_PATTERNS if cue in content[:1800])
    return min(0.080, heading_hits * 0.018 + content_hits * 0.006 + strong_hits * 0.024)


def _topic_tokens(question: str) -> set[str]:
    return {
        token for token in lexical_tokens(question)
        if len(token) >= 3 and token not in GENERIC_TOPIC_TERMS
    }


def _apply_topic_specificity(question: str, items: list[dict]) -> None:
    """Boost rare query terms so a definition of another concept cannot win.

    Common terms such as Simulink or coverage occur in most candidates and get
    little weight. A specific term such as MCDC has a small document frequency
    and therefore dominates generic "What is ..." headings.
    """
    tokens = _topic_tokens(question)
    if not tokens or not items:
        return
    item_tokens: list[set[str]] = []
    frequencies: dict[str, int] = {token: 0 for token in tokens}
    for item in items:
        text = f"{item.get('heading_path') or ''} {item.get('content') or ''}".lower()
        present = {token for token in tokens if token in text}
        item_tokens.append(present)
        for token in present:
            frequencies[token] += 1
    for item, present in zip(items, item_tokens, strict=True):
        specificity = sum(1.0 / max(1, frequencies[token]) for token in present)
        item["topic_specificity"] = round(specificity, 6)
        item["selector_score"] = round(
            float(item.get("selector_score", 0.0)) + min(0.060, specificity * 0.025),
            6,
        )


def _heading_overlap_score(question: str, item: dict, role: str) -> float:
    heading = f"{item.get('title') or ''} {item.get('heading_path') or ''}".lower()
    if not heading:
        return 0.0
    question_tokens = {
        token for token in lexical_tokens(question)
        if len(token) >= 3 and re.fullmatch(r"[a-z0-9_.:+/-]+", token)
    }
    heading_tokens = set(lexical_tokens(heading))
    overlap = len(question_tokens & heading_tokens)
    score = min(0.040, overlap * 0.010)
    if role in {"procedure", "definition_procedure"}:
        lowered_question = question.lower()
        if "import" in heading and any(cue in lowered_question for cue in ("import", "导入", "如何", "怎么")):
            score += 0.030
        if "export" in heading and any(cue in lowered_question for cue in ("export", "导出")):
            score += 0.020
        if "configure" in heading and any(cue in lowered_question for cue in ("configure", "配置")):
            score += 0.020
        if "generate" in heading and any(cue in lowered_question for cue in ("generate", "生成")):
            score += 0.020
    return min(0.080, score)


def _is_noise_candidate(item: dict) -> tuple[bool, str | None]:
    content = (item.get("content") or "").strip()
    heading = (item.get("heading_path") or "").strip().lower()
    lowered = content.lower()
    combined = f"{item.get('title') or ''} {item.get('heading_path') or ''} {content}"
    c1_controls = sum(1 for char in combined if 0x80 <= ord(char) <= 0x9F)
    if c1_controls >= 8 and c1_controls / max(1, len(combined)) >= 0.015:
        return True, "mojibake_text"
    if len(content) < 24:
        return True, "too_short"
    if "related links" in lowered and len(content) < 420:
        return True, "reference_only"
    if not heading and lowered.startswith("contents "):
        return True, "table_of_contents"
    if content.count(". . .") >= 3:
        return True, "table_of_contents"
    return False, None


def _jaccard(a: str, b: str) -> float:
    left = set(lexical_tokens(a))
    right = set(lexical_tokens(b))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _is_duplicate(item: dict, selected: list[dict]) -> tuple[bool, str | None]:
    for kept in selected:
        if item.get("chunk_id") == kept.get("chunk_id"):
            return True, "same_chunk"
        same_doc = item.get("document_id") == kept.get("document_id")
        if not same_doc:
            continue
        item_ordinal = item.get("ordinal")
        kept_ordinal = kept.get("ordinal")
        if item_ordinal is not None and kept_ordinal is not None and abs(int(item_ordinal) - int(kept_ordinal)) <= 1:
            if _jaccard(item.get("content", ""), kept.get("content", "")) >= 0.45:
                return True, "duplicate_neighbor"
        if item.get("page") and item.get("page") == kept.get("page"):
            if _jaccard(item.get("content", ""), kept.get("content", "")) >= 0.65:
                return True, "duplicate_page"
    return False, None


def _selector_score(question: str, item: dict, query_domains: set[str], role: str) -> float:
    channels = set(item.get("channels") or [])
    document_domains = set(item.get("document_domains") or [])
    score = float(item.get("final_score", item.get("rrf_score", 0.0)) or 0.0)

    if "bm25" in channels and "dense" in channels:
        score += 0.030
    elif "bm25" in channels or "dense" in channels:
        score += 0.016
    if "graph" in channels:
        score += 0.006
    if "wiki" in channels:
        score += 0.018
    if channels == {"graph"}:
        score -= 0.026
    if channels == {"wiki"}:
        score -= 0.006

    if query_domains:
        overlap = document_domains & query_domains
        if overlap:
            score += 0.030
            if query_domains <= document_domains:
                score += 0.012
            extra_domains = document_domains - query_domains
            score -= min(0.030, 0.012 * len(extra_domains))
        elif document_domains:
            score -= 0.020
        else:
            score -= 0.025

    content = item.get("content") or ""
    if len(content) < 80:
        score -= 0.010
    if len(content) > 2200:
        score -= 0.006

    lowered_question = question.lower()
    lowered_text = f"{item.get('title') or ''} {item.get('heading_path') or ''} {content}".lower()
    if (
        ("code coverage" in lowered_text or "software-in-the-loop" in lowered_text)
        and not any(cue in lowered_question for cue in ("code coverage", "sil", "pil", "代码覆盖"))
    ):
        score -= 0.045
    for term in ("autosar", "simulink", "stateflow", "solver", "fixed-step", "variable-step", "arxml"):
        if term in lowered_question and term in lowered_text:
            score += 0.010
    if role == "relationship":
        score += _relation_evidence_score(item)
    if role == "procedure":
        score += _procedure_evidence_score(item)
    elif role == "definition":
        score += _definition_evidence_score(item)
    elif role == "definition_procedure":
        score += _definition_evidence_score(item) + _procedure_evidence_score(item)
    score += _heading_overlap_score(question, item, role)
    return round(score, 6)


def select_evidence(
    question: str,
    candidates: list[dict],
    *,
    final_limit: int = 6,
    trace: dict | None = None,
) -> list[dict]:
    """Pick a compact, cleaner evidence set from hybrid retrieval candidates.

    This is intentionally deterministic for the local demo. It complements
    rerank: rerank orders by relevance, while this selector controls noise,
    duplicate chunks, source diversity, domain coverage, and graph-only risk.
    """
    query_domains = set(preferred_domains(question))
    lowered_question = question.lower()
    # Keep every strongly mentioned domain. Collapsing AUTOSAR + Test or
    # Stateflow + Test into one primary label prevents the selector from
    # reserving evidence for the second half of a cross-domain question.
    primary_domains = set(query_domains)
    roles = question_roles(question, query_domains)
    role = question_role(question, query_domains)
    aspects = requested_aspects(question)
    rejected: list[dict] = []
    eligible: list[dict] = []

    for index, item in enumerate(candidates):
        copied = dict(item)
        copied["candidate_rank"] = index + 1
        noise, reason = _is_noise_candidate(copied)
        if noise:
            rejected.append({"chunk_id": copied.get("chunk_id"), "reason": reason, "candidate_rank": index + 1})
            continue
        definition_score = _definition_evidence_score(copied)
        procedure_score = _procedure_evidence_score(copied)
        copied["definition_evidence_score"] = definition_score
        copied["procedure_evidence_score"] = procedure_score
        if definition_score > 0 and procedure_score > 0:
            copied["evidence_role"] = "definition_procedure"
        elif definition_score > 0:
            copied["evidence_role"] = "definition"
        elif procedure_score > 0:
            copied["evidence_role"] = "procedure"
        elif _relation_evidence_score(copied) > 0:
            copied["evidence_role"] = "relationship"
        else:
            copied["evidence_role"] = "general"
        copied["selector_score"] = _selector_score(question, copied, query_domains, role)
        copied["aspect_scores"] = {
            aspect.name: round(aspect_evidence_score(aspect, copied), 4)
            for aspect in aspects
        }
        if "relationship" in roles and role != "relationship":
            copied["selector_score"] = round(copied["selector_score"] + _relation_evidence_score(copied), 6)
        if "procedure" in roles and role not in {"procedure", "definition_procedure"}:
            copied["selector_score"] = round(copied["selector_score"] + _procedure_evidence_score(copied), 6)
        eligible.append(copied)

    _apply_topic_specificity(question, eligible)
    eligible.sort(key=lambda item: item["selector_score"], reverse=True)

    selected: list[dict] = []
    selected_domains: set[str] = set()
    per_document_count: dict[int, int] = defaultdict(int)
    requested_procedure_stages = _requested_procedure_stages(question) if role == "procedure" else []
    max_topic_specificity = max(
        (float(item.get("topic_specificity", 0.0)) for item in eligible),
        default=0.0,
    )
    mixed_topic_floor = max_topic_specificity * 0.75
    mixed_topic_candidates = sum(
        1 for item in eligible
        if float(item.get("topic_specificity", 0.0)) >= mixed_topic_floor
    )
    domain_matched_count = sum(
        1 for item in eligible
        if query_domains and set(item.get("document_domains") or []) & query_domains
    )
    document_quota = final_limit if len(primary_domains) <= 1 else max(2, final_limit // 2)

    def try_add(item: dict, *, force: bool = False) -> bool:
        if len(selected) >= final_limit:
            return False
        if any(item.get("chunk_id") == kept.get("chunk_id") for kept in selected):
            return False
        document_id = int(item.get("document_id") or 0)
        item_domains = set(item.get("document_domains") or [])
        if (
            role == "definition_procedure"
            and len(selected) >= 2
            and mixed_topic_candidates >= 2
            and float(item.get("topic_specificity", 0.0)) < mixed_topic_floor
        ):
            rejected.append({
                "chunk_id": item.get("chunk_id"),
                "reason": "off_topic_after_mixed_core",
                "candidate_rank": item.get("candidate_rank"),
            })
            return False
        off_domain = bool(primary_domains and (not item_domains or not (item_domains & primary_domains)))
        if off_domain and domain_matched_count >= min(4, final_limit) and len(selected) >= min(4, final_limit):
            rejected.append({"chunk_id": item.get("chunk_id"), "reason": "off_domain_after_enough_core", "candidate_rank": item.get("candidate_rank")})
            return False
        if not force and per_document_count[document_id] >= document_quota:
            rejected.append({"chunk_id": item.get("chunk_id"), "reason": "document_quota", "candidate_rank": item.get("candidate_rank")})
            return False
        duplicate, reason = _is_duplicate(item, selected)
        if duplicate and not force:
            rejected.append({"chunk_id": item.get("chunk_id"), "reason": reason, "candidate_rank": item.get("candidate_rank")})
            return False
        selected.append(item)
        per_document_count[document_id] += 1
        selected_domains.update(item.get("document_domains") or [])
        return True

    # Compound engineering questions need one strong item per requested
    # aspect. This happens before ordinary score filling so four similar pages
    # about importing a model cannot crowd out mapping or generation evidence.
    for aspect in aspects:
        aspect_item = max(
            (
                item for item in eligible
                if item not in selected and item.get("aspect_scores", {}).get(aspect.name, 0.0) >= 0.28
            ),
            key=lambda item: (
                item.get("aspect_scores", {}).get(aspect.name, 0.0),
                -len(set(item.get("document_domains") or []) - primary_domains),
                bool(set(item.get("channels") or []) & {"bm25", "dense"}),
                item.get("selector_score", 0.0),
            ),
            default=None,
        )
        if aspect_item:
            try_add(aspect_item, force=True)
    all_aspects_selected = bool(aspects) and all(
        any(item.get("aspect_scores", {}).get(aspect.name, 0.0) >= 0.28 for item in selected)
        for aspect in aspects
    )

    # A workflow answer needs evidence from the requested stages instead of
    # six high-scoring chunks about the same operation. Expansion-heading
    # agreement is the first tie-breaker so cross-language manual headings
    # win over generic procedure wording from adjacent products.
    if role == "procedure":
        for stage in requested_procedure_stages:
            if any(_procedure_stage(item) == stage for item in selected):
                continue
            stage_item = max(
                (item for item in eligible if item not in selected and _procedure_stage(item) == stage),
                key=lambda item: (
                    -len(set(item.get("document_domains") or []) - primary_domains),
                    bool(set(item.get("channels") or []) & {"bm25", "dense"}),
                    item.get("expansion_heading_boost", 0),
                    item.get("topic_specificity", 0),
                    item.get("selector_score", 0),
                ),
                default=None,
            )
            if stage_item:
                try_add(stage_item, force=True)

    # Mixed definition/procedure questions need both kinds of support. Without
    # this constraint, action-heavy chunks can crowd out an already-retrieved
    # definition and make the answer describe tools instead of the concept.
    if role == "definition_procedure" and not all_aspects_selected:
        definition_candidates = [
            item for item in eligible if item.get("definition_evidence_score", 0) > 0
        ]
        definition_item = max(
            definition_candidates,
            key=lambda item: (
                item.get("topic_specificity", 0),
                item.get("definition_evidence_score", 0),
                item.get("selector_score", 0),
            ),
            default=None,
        )
        if definition_item:
            try_add(definition_item, force=True)
        procedure_candidates = [
            item for item in eligible
            if item not in selected and item.get("procedure_evidence_score", 0) > 0
        ]
        procedure_item = max(
            procedure_candidates,
            key=lambda item: (
                item.get("topic_specificity", 0),
                item.get("procedure_evidence_score", 0),
                item.get("selector_score", 0),
            ),
            default=None,
        )
        if procedure_item:
            try_add(procedure_item, force=True)

    # Multi-domain questions should get at least one piece of evidence from
    # each strongly matched document domain when available.
    if len(primary_domains) >= 2:
        for domain in sorted(primary_domains):
            if any(domain in set(item.get("document_domains") or []) for item in selected):
                continue
            domain_item = next(
                (item for item in eligible if domain in set(item.get("document_domains") or []) and item not in selected),
                None,
            )
            if domain_item:
                try_add(domain_item, force=True)
        if role == "relationship":
            relation_item = next(
                (
                    item for item in eligible
                    if item not in selected
                    and item.get("evidence_role") == "relationship"
                    and set(item.get("document_domains") or []) & primary_domains
                ),
                None,
            )
            if relation_item:
                try_add(relation_item, force=True)

    if all_aspects_selected and len(aspects) >= 2:
        minimum_evidence = min(max(2, len(selected)), final_limit)
    elif role == "definition_procedure":
        minimum_evidence = min(2, final_limit)
    elif role == "procedure" and requested_procedure_stages:
        minimum_evidence = min(max(2, len(requested_procedure_stages)), final_limit)
    else:
        minimum_evidence = min(4, final_limit)
    if all_aspects_selected and len(aspects) >= 2:
        selection_target = minimum_evidence
    else:
        selection_target = minimum_evidence if role == "procedure" and requested_procedure_stages else final_limit

    for item in eligible:
        if len(selected) >= selection_target:
            break
        if item in selected:
            continue
        try_add(item)
        if len(selected) >= selection_target:
            break

    # If de-duplication was too strict, fill the remaining slots with the best
    # safe candidates so the answer model still has enough context.
    if len(selected) < minimum_evidence:
        for item in eligible:
            if item in selected:
                continue
            try_add(item, force=True)
            if len(selected) >= minimum_evidence:
                break

    selected_ids = {item.get("chunk_id") for item in selected}
    for item in eligible:
        if item.get("chunk_id") not in selected_ids and not any(row.get("chunk_id") == item.get("chunk_id") for row in rejected):
            rejected.append({
                "chunk_id": item.get("chunk_id"),
                "reason": "below_selected_cut",
                "candidate_rank": item.get("candidate_rank"),
            })

    if trace is not None:
        selected_ids_for_trace = {item.get("chunk_id") for item in selected}
        trace["evidence_selector"] = {
            "enabled": True,
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "selected_count": len(selected),
            "question_role": role,
            "question_roles": sorted(roles),
            "requested_aspects": [aspect.name for aspect in aspects],
            "covered_aspects": sorted({
                aspect.name
                for aspect in aspects
                if any(item.get("aspect_scores", {}).get(aspect.name, 0.0) >= 0.28 for item in selected)
            }),
            "query_domains": sorted(query_domains),
            "primary_domains": sorted(primary_domains),
            "selected_domains": sorted(selected_domains),
            "selected": [
                {
                    "chunk_id": item.get("chunk_id"),
                    "document_id": item.get("document_id"),
                    "title": item.get("title"),
                    "channels": item.get("channels"),
                    "document_domains": item.get("document_domains"),
                    "selector_score": item.get("selector_score"),
                    "topic_specificity": item.get("topic_specificity"),
                    "evidence_role": item.get("evidence_role"),
                    "aspect_scores": item.get("aspect_scores", {}),
                    "candidate_rank": item.get("candidate_rank"),
                }
                for item in selected
            ],
            "rejected": [item for item in rejected if item.get("chunk_id") not in selected_ids_for_trace][:30],
        }

    return selected
