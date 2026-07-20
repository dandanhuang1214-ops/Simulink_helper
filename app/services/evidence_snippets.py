from __future__ import annotations

import re

from app.services.domains import preferred_domains
from app.services.evidence_selector import _requested_procedure_stages, question_role
from app.services.question_aspects import requested_aspects
from app.services.text import lexical_tokens


ROLE_EVIDENCE_LIMITS = {
    "definition": 2,
    "definition_procedure": 5,
    "relationship": 4,
    "comparison": 4,
    "procedure": 5,
    "general": 4,
}

ROLE_TOKEN_BUDGETS = {
    "definition": 110,
    "definition_procedure": 300,
    "relationship": 300,
    "comparison": 320,
    "procedure": 400,
    "general": 260,
}


def answer_generation_budget(question: str) -> int:
    role = question_role(question, preferred_domains(question))
    budget = ROLE_TOKEN_BUDGETS.get(role, ROLE_TOKEN_BUDGETS["general"])
    if len(requested_aspects(question)) >= 3:
        return max(budget, 420)
    return budget


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


def _snippet_for_item(
    question: str,
    item: dict,
    *,
    max_chars: int = 460,
    proxy_queries: list[str] | None = None,
) -> str:
    content = item.get("content") or ""
    units = _split_units(content)
    if not units:
        return ""
    question_tokens = set(lexical_tokens(question))
    content_tokens = set(lexical_tokens(content))
    chinese_question = bool(re.search(r"[\u4e00-\u9fff]", question))
    english_content = len(re.findall(r"[A-Za-z]", content)) > max(
        40,
        2 * len(re.findall(r"[\u4e00-\u9fff]", content)),
    )
    # With a Chinese question and an English manual, direct token overlap often
    # collapses to product names such as “Simulink”. Use the parser-preserved
    # English heading as a cross-language relevance proxy. This keeps the unit
    # describing "View Simulation Data" instead of blindly taking a preceding
    # page continuation, while remaining independent of frozen chunk IDs.
    if chinese_question and english_content:
        if len(question_tokens & content_tokens) <= 2:
            question_tokens.update(lexical_tokens(item.get("heading_path") or ""))
        # Retrieval facets contain the cross-language vocabulary for every
        # requested aspect (for example internally/externally). Use them even
        # when product names already overlap, otherwise generic Simulink/Test
        # tokens can hide the precise sentence needed by the answer.
        question_tokens.update(lexical_tokens(" ".join(proxy_queries or [])))
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
    aspects = requested_aspects(question)
    if len(aspects) >= 2:
        # The deterministic selector has already ordered these by requested
        # aspect. Reclassifying them with generic definition/procedure cues can
        # move an import example ahead of the actual definition.
        return evidence[:limit], role
    if role == "procedure" and not _requested_procedure_stages(question):
        limit = min(limit, 3)
    query_domains = preferred_domains(question)
    selected: list[dict] = []

    if role == "definition_procedure":
        definition = next(
            (
                item for item in evidence
                if item.get("evidence_role") in {"definition", "definition_procedure"}
            ),
            None,
        )
        if definition:
            selected.append(definition)
        procedure = next(
            (
                item for item in evidence
                if item not in selected
                and item.get("evidence_role") in {"procedure", "definition_procedure"}
            ),
            None,
        )
        if procedure:
            selected.append(procedure)

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
        snippet_limit = 300 if role == "definition" else 460
        proxy_queries = list((trace or {}).get("queries") or [])
        item_aspect_scores = item.get("aspect_scores") or {}
        if item_aspect_scores:
            best_aspect = max(item_aspect_scores, key=item_aspect_scores.get)
            aspect = next(
                (value for value in requested_aspects(question) if value.name == best_aspect),
                None,
            )
            if aspect is not None:
                proxy_queries = [aspect.facet]
        snippet = _snippet_for_item(
            question,
            item,
            max_chars=snippet_limit,
            proxy_queries=proxy_queries,
        )
        compact = dict(item)
        compact["prompt_snippet"] = snippet
        compact_items.append(compact)
        page = item.get("page") or "unknown"
        heading = item.get("heading_path") or ""
        title = item.get("title") or "unknown source"
        supports = [
            name for name, score in (item.get("aspect_scores") or {}).items()
            if float(score) >= 0.28
        ]
        supports_text = f"; supports={','.join(supports)}" if supports else ""
        lines.append(
            f"[E:{item.get('chunk_id')}] source={title}; page={page}; heading={heading}{supports_text}\n{snippet}"
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


def role_answer_contract(role: str, question: str = "") -> str:
    if role == "definition":
        return (
            "最多用 3 句简洁中文回答：先给出一句定义，再说明一个核心用途或与 Simulink 的关系。"
            "不要扩展操作步骤、文件导入、操作点、调试功能或背景材料，除非问题明确询问。"
            "每一句事实性结论都必须在该句句末立即带 [E:n] 引用；第一句定义也不能省略引用，"
            "不要把第一句的引用延后到下一句。"
        )
    if role == "definition_procedure":
        contract = (
            "总长度不超过 350 个汉字。先用 1 句说明概念定义，再对问题实际要求的操作、映射、同步或管理方面各用 1 句回答；"
            "必须先覆盖完所有被问到的方面，禁止展开示例、背景或重复解释。"
            "定义和各个被问到的方面必须分别有直接证据 [E:n]，不要用相关工具、导入示例或报告功能替代定义。"
            "不要把‘隔离的测试环境’夸大成‘与主模型完全解耦’，也不要声称同步永远自动发生。"
            "不得推断证据没有说明的内存共享、实时数据流或底层实现方式。"
        )
        if "harness" in question.casefold() or "测试夹具" in question:
            contract += "定义必须使用‘为被测组件提供隔离的测试环境’这一中性表述。"
        aspect_requirements = [
            aspect.answer_requirement for aspect in requested_aspects(question)
            if aspect.answer_requirement
        ]
        if aspect_requirements:
            contract += "必须逐项覆盖：" + "；".join(aspect_requirements) + "。"
        return contract
    if role == "relationship":
        return (
            "最多用 3 个小段说明：核心关系、映射/集成方式、证据边界。"
            "保持简洁，每一句事实性结论都必须带 [E:n] 引用。"
        )
    if role == "comparison":
        return "用紧凑中文对比，只写证据支持的差异点。每一句事实性结论都必须带 [E:n] 引用。"
    if role == "procedure":
        return (
            "最多用 4 个编号步骤或要点回答，总长度尽量控制在 500 个汉字内；每步最多两句，并带 [E:n] 引用。"
            "只描述证据明确支持的动作；不要把 Related Links、See Also 或章节标题脑补成完整操作。"
            "把可选方式写成并列选项，不要拼接成必须依次执行的单一路径；不要把示例中的名称泛化为固定名称。"
            "限制条件要按原文准确复述，不要把‘某些对象不支持’改写成‘未启用这些对象就不支持’；证据出现许可要求时必须说明。"
            "如果证据只支持概念流程，就明确说明“现有证据只能确认概念流程/工具入口，不能确认具体 UI 步骤”。"
            "除非证据原文明确出现 click/select/open/save 等操作，否则不要写“点击、选择、保存、系统提示、弹窗”。"
        )
    return "最多用 4 个简洁中文要点或短段落回答。每一句事实性结论都必须带 [E:n] 引用。"
