from __future__ import annotations

import re
from collections import defaultdict

from app.services.domains import preferred_domains
from app.services.text import lexical_tokens


RELATION_QUERY_CUES = (
    "relationship", "relation", "relate", "connect", "integration", "integrate", "mapping", "map",
    "import", "export", "interface", "interact", "between",
    "关系", "关联", "联系", "连接", "集成", "映射", "导入", "导出", "接口", "交互", "之间",
)
COMPARISON_QUERY_CUES = ("compare", "difference", "versus", "vs", "区别", "对比", "比较", "差异")
PROCEDURE_QUERY_CUES = ("how", "steps", "workflow", "process", "怎么", "如何", "步骤", "流程")
DEFINITION_QUERY_CUES = ("what is", "define", "definition", "是什么", "定义")

RELATION_EVIDENCE_CUES = (
    "relationship", "related", "relate", "connect", "connection", "integration", "integrate",
    "mapping", "mapped", "map", "import", "export", "interface", "interact", "composition",
    "component", "arxml", "runnable", "port", "signal", "subsystem", "model reference",
    "关系", "关联", "联系", "连接", "集成", "映射", "导入", "导出", "接口", "交互", "组成", "组件", "端口", "信号",
)


def question_role(question: str, query_domains: set[str] | None = None) -> str:
    lowered = question.lower()
    domains = query_domains if query_domains is not None else preferred_domains(question)
    if len(domains) >= 2 and any(cue in lowered for cue in RELATION_QUERY_CUES):
        return "relationship"
    if any(cue in lowered for cue in COMPARISON_QUERY_CUES):
        return "comparison"
    if any(cue in lowered for cue in PROCEDURE_QUERY_CUES):
        return "procedure"
    if any(cue in lowered for cue in DEFINITION_QUERY_CUES):
        return "definition"
    return "general"


def _relation_evidence_score(item: dict) -> float:
    text = f"{item.get('title') or ''} {item.get('heading_path') or ''} {item.get('content') or ''}".lower()
    hits = sum(1 for cue in RELATION_EVIDENCE_CUES if cue in text)
    return min(0.045, hits * 0.009)


def _is_noise_candidate(item: dict) -> tuple[bool, str | None]:
    content = (item.get("content") or "").strip()
    heading = (item.get("heading_path") or "").strip().lower()
    lowered = content.lower()
    if len(content) < 24:
        return True, "too_short"
    if not heading and lowered.startswith("contents "):
        return True, "table_of_contents"
    if content.count(". . .") >= 3:
        return True, "table_of_contents"
    if len(re.findall(r"\b\d+-\d+\b", content)) >= 8:
        return True, "page_index_noise"
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

    if query_domains and document_domains & query_domains:
        score += 0.030
    elif query_domains and document_domains and not (document_domains & query_domains):
        score -= 0.020

    content = item.get("content") or ""
    if len(content) < 80:
        score -= 0.010
    if len(content) > 2200:
        score -= 0.006

    lowered_question = question.lower()
    lowered_text = f"{item.get('title') or ''} {item.get('heading_path') or ''} {content}".lower()
    for term in ("autosar", "simulink", "stateflow", "solver", "fixed-step", "variable-step", "arxml"):
        if term in lowered_question and term in lowered_text:
            score += 0.010
    if role == "relationship":
        score += _relation_evidence_score(item)
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
    primary_domains = set(query_domains)
    if "autosar" in query_domains and ("arxml" in lowered_question or "autosar" in lowered_question):
        primary_domains = {"autosar"}
    role = question_role(question, query_domains)
    rejected: list[dict] = []
    eligible: list[dict] = []

    for index, item in enumerate(candidates):
        copied = dict(item)
        copied["candidate_rank"] = index + 1
        noise, reason = _is_noise_candidate(copied)
        if noise:
            rejected.append({"chunk_id": copied.get("chunk_id"), "reason": reason, "candidate_rank": index + 1})
            continue
        copied["evidence_role"] = "relationship" if _relation_evidence_score(copied) > 0 else "general"
        copied["selector_score"] = _selector_score(question, copied, query_domains, role)
        eligible.append(copied)

    eligible.sort(key=lambda item: item["selector_score"], reverse=True)

    selected: list[dict] = []
    selected_domains: set[str] = set()
    per_document_count: dict[int, int] = defaultdict(int)
    domain_matched_count = sum(
        1 for item in eligible
        if query_domains and set(item.get("document_domains") or []) & query_domains
    )
    document_quota = final_limit if len(primary_domains) <= 1 else max(2, final_limit // 2)

    def try_add(item: dict, *, force: bool = False) -> bool:
        if len(selected) >= final_limit:
            return False
        document_id = int(item.get("document_id") or 0)
        item_domains = set(item.get("document_domains") or [])
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

    # Multi-domain questions should get at least one piece of evidence from
    # each strongly matched document domain when available.
    if len(primary_domains) >= 2:
        for domain in sorted(primary_domains):
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

    for item in eligible:
        if item in selected:
            continue
        try_add(item)
        if len(selected) >= final_limit:
            break

    # If de-duplication was too strict, fill the remaining slots with the best
    # safe candidates so the answer model still has enough context.
    if len(selected) < min(4, final_limit):
        for item in eligible:
            if item in selected:
                continue
            try_add(item, force=True)
            if len(selected) >= min(4, final_limit):
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
                    "evidence_role": item.get("evidence_role"),
                    "candidate_rank": item.get("candidate_rank"),
                }
                for item in selected
            ],
            "rejected": [item for item in rejected if item.get("chunk_id") not in selected_ids_for_trace][:30],
        }

    return selected
