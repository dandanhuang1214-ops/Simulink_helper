from __future__ import annotations

import re

from app.services.domains import preferred_domains
from app.services.evidence_selector import question_role
from app.services.text import lexical_tokens


ROLE_EVIDENCE_LIMITS = {
    "definition": 3,
    "relationship": 4,
    "comparison": 4,
    "procedure": 5,
    "general": 4,
}

ROLE_TOKEN_BUDGETS = {
    "definition": 150,
    "relationship": 340,
    "comparison": 380,
    "procedure": 440,
    "general": 320,
}


def answer_generation_budget(question: str) -> int:
    role = question_role(question, preferred_domains(question))
    return ROLE_TOKEN_BUDGETS.get(role, ROLE_TOKEN_BUDGETS["general"])


def _split_units(content: str) -> list[str]:
    text = re.sub(r"\s+", " ", (content or "").strip())
    if not text:
        return []
    units = [
        part.strip()
        for part in re.split(r"(?<=[.!?。！？；;])\s+|\n+|(?<=。)|(?<=！)|(?<=？)|(?<=；)", text)
        if part.strip()
    ]
    if len(units) <= 1 and len(text) > 220:
        units = [text[index:index + 220].strip() for index in range(0, len(text), 220)]
    return units


def _unit_score(question_tokens: set[str], unit: str, position: int) -> float:
    unit_tokens = set(lexical_tokens(unit))
    if not unit_tokens:
        return 0.0
    overlap = len(question_tokens & unit_tokens)
    density = overlap / max(1, len(unit_tokens))
    score = overlap * 2.0 + density
    if position <= 1:
        score += 0.35
    if any(mark in unit for mark in (":", "：", "->", "→", "maps", "mapping", "import", "export")):
        score += 0.25
    return score


def _snippet_for_item(question: str, item: dict, *, max_chars: int = 460) -> str:
    content = item.get("content") or ""
    units = _split_units(content)
    if not units:
        return ""
    question_tokens = set(lexical_tokens(question))
    ranked = sorted(
        enumerate(units),
        key=lambda pair: _unit_score(question_tokens, pair[1], pair[0]),
        reverse=True,
    )
    picked: list[tuple[int, str]] = []
    total = 0
    for index, unit in ranked[:6]:
        trimmed = unit.strip()
        if len(trimmed) < 12:
            continue
        if total + len(trimmed) > max_chars and picked:
            continue
        picked.append((index, trimmed))
        total += len(trimmed)
        if len(picked) >= 3 or total >= max_chars:
            break
    if not picked:
        return content[:max_chars].strip()
    ordered = [unit for _, unit in sorted(picked, key=lambda pair: pair[0])]
    snippet = " ".join(ordered).strip()
    return snippet[:max_chars].rstrip()


def _domain_set(item: dict) -> set[str]:
    return set(item.get("document_domains") or [])


def select_prompt_evidence(question: str, evidence: list[dict]) -> tuple[list[dict], str]:
    role = question_role(question, preferred_domains(question))
    limit = ROLE_EVIDENCE_LIMITS.get(role, ROLE_EVIDENCE_LIMITS["general"])
    query_domains = preferred_domains(question)
    selected: list[dict] = []

    if role in {"relationship", "comparison"} and len(query_domains) >= 2:
        for domain in sorted(query_domains):
            match = next(
                (item for item in evidence if item not in selected and domain in _domain_set(item)),
                None,
            )
            if match:
                selected.append(match)

    for item in evidence:
        if len(selected) >= limit:
            break
        if item not in selected:
            selected.append(item)

    return selected[:limit], role


def build_compact_context(question: str, evidence: list[dict], trace: dict | None = None) -> tuple[str, list[dict], str]:
    prompt_evidence, role = select_prompt_evidence(question, evidence)
    lines: list[str] = []
    compact_items: list[dict] = []
    original_chars = sum(len(item.get("content") or "") for item in prompt_evidence)

    for item in prompt_evidence:
        snippet = _snippet_for_item(question, item)
        compact = dict(item)
        compact["prompt_snippet"] = snippet
        compact_items.append(compact)
        page = item.get("page") or "unknown"
        heading = item.get("heading_path") or ""
        title = item.get("title") or "unknown source"
        lines.append(
            f"[E:{item.get('chunk_id')}] source={title}; page={page}; heading={heading}\n{snippet}"
        )

    context = "\n\n".join(lines)
    if trace is not None:
        trace["prompt_compaction"] = {
            "enabled": True,
            "question_role": role,
            "input_evidence_count": len(evidence),
            "prompt_evidence_count": len(prompt_evidence),
            "original_chars": original_chars,
            "compact_chars": len(context),
            "chunk_ids": [item.get("chunk_id") for item in prompt_evidence],
        }
    return context, compact_items, role


def role_answer_contract(role: str) -> str:
    if role == "definition":
        return "用 2-3 句简洁中文回答，不要展开背景材料。每一句事实性结论都必须带 [E:n] 引用。"
    if role == "relationship":
        return (
            "最多用 3 个小段说明：核心关系、映射/集成方式、证据边界。"
            "保持简洁，每一句事实性结论都必须带 [E:n] 引用。"
        )
    if role == "comparison":
        return "用紧凑中文对比，只写证据支持的差异点。每一句事实性结论都必须带 [E:n] 引用。"
    if role == "procedure":
        return "最多用 4 个编号步骤或要点回答。每个步骤都必须带 [E:n] 引用。"
    return "最多用 4 个简洁中文要点或短段落回答。每一句事实性结论都必须带 [E:n] 引用。"
