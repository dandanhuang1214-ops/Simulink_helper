from __future__ import annotations

import json
import re

from app.config import get_settings
from app.database import Evaluation, SessionLocal
from app.services.coverage import insufficient_coverage_answer
from app.services.evidence_snippets import (
    answer_generation_budget,
    build_compact_context,
    role_answer_contract,
)
from app.services.ollama import OllamaClient
from app.services.retrieval_pipeline import retrieve_evidence_with_coverage
from app.services.text import lexical_tokens


DOMAIN_TERMS = [
    "simulink", "autosar", "matlab", "stateflow", "mil", "sil", "hil", "arxml",
    "求解器", "仿真", "代码生成", "模型", "测试", "状态机", "模块", "组件", "端口",
    "信号", "总线", "步长", "固定步长", "可变步长", "嵌入式", "标定", "需求",
]

GREETING_PATTERNS = [
    "你好", "您好", "在吗", "在不在", "有人吗", "哈喽", "哈啰", "嗨", "早上好", "晚上好",
    "hello", "hi", "hey", "yo",
]

ENDING_PARTICLES = "啊呀呢嘛吗啦了哦噢哟哈喔欸诶呗吧喽"


def _normalize(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[。！？?.,，；;：:、（）()\[\]《》\"'“”‘’·…—\\-]+", "", text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    return text


def _strip_ending_particles(value: str) -> str:
    text = value
    while len(text) > 1 and text[-1] in ENDING_PARTICLES:
        text = text[:-1]
    return text


def _contains_domain_term(value: str) -> bool:
    return any(term in value for term in DOMAIN_TERMS)


def _is_greeting_turn(normalized: str) -> bool:
    if _contains_domain_term(normalized):
        return False
    compact = _strip_ending_particles(normalized)
    if compact in GREETING_PATTERNS:
        return True
    if len(compact) <= 8 and any(pattern in compact for pattern in GREETING_PATTERNS):
        return True
    return False


def _direct_payload(answer: str, intent: str) -> dict:
    return {
        "answer": answer,
        "citations": [],
        "evaluation": {"passed": True, "mode": "direct", "intent": intent},
        "evidence": [],
        "mode": "direct",
        "intent": intent,
    }


def _refusal_payload(answer: str, intent: str) -> dict:
    return {
        "answer": answer,
        "citations": [],
        "evaluation": {"passed": False, "mode": "refusal", "intent": intent, "reason": intent},
        "evidence": [],
        "mode": "refusal",
        "intent": intent,
    }


def citation_dicts(answer: str, evidence: list[dict]) -> list[dict]:
    return [
        {key: item[key] for key in ("chunk_id", "document_id", "title", "page", "bbox")}
        for item in evidence if f"[E:{item['chunk_id']}]" in answer
    ]


def _drop_uncited_extra_paragraphs(answer: str) -> str:
    if not re.search(r"\[E:\d+\]", answer):
        return answer
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", answer.strip()) if part.strip()]
    if len(paragraphs) <= 1:
        return answer
    kept: list[str] = []
    for paragraph in paragraphs:
        if re.search(r"\[E:\d+\]", paragraph):
            if kept and _looks_like_copied_english_evidence(paragraph):
                continue
            kept.append(paragraph)
            continue
        # Keep compact Chinese section headings, but drop copied evidence tails
        # or uncited factual paragraphs.
        if len(paragraph) <= 24 and not re.search(r"[.!?。！？]", paragraph):
            kept.append(paragraph)
    return "\n\n".join(kept) if kept else answer


def _looks_like_copied_english_evidence(paragraph: str) -> bool:
    letters = re.findall(r"[A-Za-z]+", paragraph)
    chinese = re.findall(r"[\u4e00-\u9fff]", paragraph)
    letter_count = sum(len(item) for item in letters)
    # A Chinese answer may contain English product names. This only catches
    # long copied evidence-like tails with almost no Chinese explanation.
    return letter_count >= 80 and len(chinese) < 12


def _align_definition_sentence_citations(answer: str, evidence: list[dict]) -> str:
    """Attach an already-selected source to an uncited definition sentence.

    This is deliberately limited to short definition answers.  A citation is
    only added when the sentence and evidence share at least two lexical
    anchors, so the formatter does not manufacture support for unrelated text.
    """
    if not answer or not evidence:
        return answer
    # Small local models sometimes emit ``sentence。[E:n] next sentence``.
    # Treat that citation as belonging to the sentence before the punctuation
    # and normalize it before checking for missing sentence-level citations.
    answer = re.sub(
        r"([。！？])\s*((?:\[E:\d+\]\s*)+)",
        lambda match: f" {match.group(2).strip()}{match.group(1)} ",
        answer,
    ).strip()
    parts = re.split(r"(?<=[。！？])", answer)
    aligned: list[str] = []
    for part in parts:
        if not part.strip() or re.search(r"\[E:\d+\]", part):
            aligned.append(part)
            continue
        sentence_tokens = set(lexical_tokens(part))
        best_item: dict | None = None
        best_overlap = 0
        for item in evidence:
            evidence_tokens = set(lexical_tokens(
                f"{item.get('title') or ''} {item.get('heading_path') or ''} {item.get('content') or ''}"
            ))
            overlap = len(sentence_tokens & evidence_tokens)
            if overlap > best_overlap:
                best_item, best_overlap = item, overlap
        if best_item is None or best_overlap < 2:
            aligned.append(part)
            continue
        body = part.rstrip()
        trailing = part[len(body):]
        if body and body[-1] in "。！？":
            body = f"{body[:-1].rstrip()} [E:{best_item['chunk_id']}]{body[-1]}"
        else:
            body = f"{body} [E:{best_item['chunk_id']}]"
        aligned.append(body + trailing)
    return "".join(aligned)


def ensure_evidence_citations(
    answer: str,
    evidence: list[dict],
    fallback_count: int = 3,
    *,
    require_sentence_citations: bool = False,
) -> tuple[str, list[dict]]:
    answer = _normalize_evidence_citation_marks(answer, evidence)
    answer = _drop_uncited_extra_paragraphs(answer)
    if require_sentence_citations:
        answer = _align_definition_sentence_citations(answer, evidence)
    citations = citation_dicts(answer, evidence)
    if citations or not evidence:
        return answer, citations
    fallback = evidence[:fallback_count]
    refs = " ".join(f"[E:{item['chunk_id']}]" for item in fallback)
    answer = f"{answer.rstrip()}\n\n主要依据：{refs}"
    return answer, [
        {key: item[key] for key in ("chunk_id", "document_id", "title", "page", "bbox")}
        for item in fallback
    ]


def _normalize_evidence_citation_marks(answer: str, evidence: list[dict]) -> str:
    if not evidence:
        return re.sub(r"\[E:[^\]]+\]", "", answer)
    allowed = {int(item["chunk_id"]) for item in evidence if item.get("chunk_id") is not None}
    fallback_id = int(evidence[0]["chunk_id"])
    last_valid: int | None = None

    def replace(match: re.Match[str]) -> str:
        nonlocal last_valid
        raw = match.group(1).strip()
        if raw.isdigit():
            value = int(raw)
            if value in allowed:
                last_valid = value
                return f"[E:{value}]"
        replacement = last_valid or fallback_id
        return f"[E:{replacement}]"

    return re.sub(r"\[E:([^\]]+)\]", replace, answer)


def conversational_reply(question: str) -> dict | None:
    """Deterministic router for product/help/chat turns.

    It keeps obvious non-knowledge turns out of RAG without pretending to solve
    open-ended intent classification. Domain terms always fall through to RAG.
    """
    normalized = _normalize(question)

    if (
        any(key in normalized for key in ["当前知识库", "知识库", "已入库", "资料库"])
        and any(key in normalized for key in ["是否包含", "有没有", "是否有", "包含"])
        and any(key in normalized for key in ["第三方", "专有", "私有", "专属", "r2027a", "未导入"])
    ):
        return _refusal_payload(
            "当前知识库不能确认覆盖这个范围。已入库资料主要来自你导入的 Simulink、Stateflow、AUTOSAR 等文档；"
            "如果问题涉及第三方工具专有配置、私有资料或未导入版本，我不能基于现有证据可靠回答。"
            "你可以先导入对应官方文档或专有工具手册，再让我按证据回答。",
            "knowledge_scope_boundary",
        )

    if _is_greeting_turn(normalized):
        return _direct_payload(
            "你好！我是本地 Simulink LLM-Wiki 助手。你可以直接问我 Simulink、Stateflow、AUTOSAR、求解器、仿真或代码生成相关问题；"
            "我会尽量基于本地知识库证据回答，并给出可追溯引用。",
            "greeting",
        )

    if any(key in normalized for key in ["你是谁", "你是什么", "介绍一下你", "你叫啥"]):
        return _direct_payload(
            "我是一个本地运行的 Simulink 知识助手 Demo。当前后端使用 FastAPI、SQLite、Qdrant 和 Ollama，前端是 Next.js。"
            "我的回答优先来自你导入的本地知识库，并尽量给出 `[E:n]` 证据引用，方便回到原始文档核查。",
            "identity",
        )

    if any(key in normalized for key in [
        "你有什么功能", "你可以做什么", "你能做什么", "你会做什么", "能干什么",
        "功能介绍", "怎么用", "使用方法", "你能帮我什么", "可以帮我什么",
    ]):
        return _direct_payload(
            "我现在主要有这些功能：\n\n"
            "1. 上传并导入资料：支持 MD、TXT、DOCX、普通 PDF；扫描 PDF/OCR 会在后续增强。\n"
            "2. 构建知识库：保留 raw 原文，解析页面、目录和证据块，并写入 SQLite FTS 与 Qdrant。\n"
            "3. 生成 LLM-Wiki：按章节生成 Wiki 草稿，每个结论都要求带 `[E:n]` 证据引用。\n"
            "4. 证据式问答：先混合检索，再生成回答，右侧展示证据和 PDF 原页。\n"
            "5. 后台评估：回答生成后异步检查检索充分性、事实一致性、引用覆盖和完整度。\n\n"
            "你可以试着问：`Stateflow 是什么？它和 Simulink 的关系是什么？` 或 `固定步长求解器适合什么场景？`",
            "capabilities",
        )

    if any(key in normalized for key in ["怎么上传", "上传文档", "导入文档", "知识库怎么构建", "怎么入库"]):
        return _direct_payload(
            "上传流程是：进入“文档”页面，选择文件和解析模式，然后点“上传并编译”。后台会依次执行：\n\n"
            "`parsing` 解析原文件 → `chunking` 切分证据块 → `indexing` 写入 SQLite/Qdrant → "
            "`wiki` 生成 Wiki 草稿 → `completed` 完成。\n\n"
            "raw 原文件会不可变保存，后续 parsed、evidence、wiki 都可以重建。",
            "upload_help",
        )

    if any(key in normalized for key in ["引用怎么看", "证据怎么看", "e:n", "原文在哪", "pdf原页"]):
        return _direct_payload(
            "回答里的 `[E:n]` 是证据块编号。点击它后，右侧会选中对应证据，展示来源文档、页码和 chunk 内容；"
            "如果是 PDF，还会显示对应原页图片。后续我们还可以加 bbox 高亮，让原文位置更精确。",
            "citation_help",
        )

    if any(key in normalized for key in ["需要复核", "证据校验", "已通过证据检查", "后台评估", "judge"]):
        return _direct_payload(
            "“已通过证据检查 / 需要复核”是后台 Judge 的质量标签。它会看四项：检索充分性、事实一致性、引用覆盖、完整度。"
            "当前 Judge 也是本地 2B 模型，所以它只是 demo 观测指标，不是绝对裁判。显示“需要复核”通常表示证据不足、引用覆盖不够，"
            "或本地 Judge 判断偏保守。",
            "judge_help",
        )

    smalltalk_patterns = ["天气", "讲个笑话", "写首诗", "闲聊", "今天星期几", "新闻", "股票", "电影"]
    if any(key in normalized for key in smalltalk_patterns) and not _contains_domain_term(normalized):
        return _direct_payload(
            "我可以简单聊两句，但当前 Demo 的重点是本地 Simulink/AUTOSAR/Stateflow 知识库。"
            "为了保证回答可追溯，建议你问和已导入资料相关的问题；如果你想测试闲聊体验，后面可以单独加一个通用助手模式。",
            "out_of_scope_chat",
        )

    return None


async def answer_question(question: str) -> dict:
    direct = conversational_reply(question)
    if direct:
        return direct
    settings = get_settings()
    trace: dict = {}
    retrieval = await retrieve_evidence_with_coverage(question, use_rewrite=False, trace=trace)
    evidence = retrieval.evidence
    if not evidence:
        return {
            "answer": "当前知识库中没有足够证据回答这个问题。你可以换个问法、取消资料筛选，或先导入相关文档。",
            "citations": [],
            "evaluation": {"passed": False, "reason": "retrieval_empty"},
            "evidence": [],
        }
    coverage = retrieval.coverage
    trace["coverage"] = {
        "passed": coverage.passed,
        "required_terms": coverage.required_terms,
        "covered_terms": coverage.covered_terms,
        "missing_terms": coverage.missing_terms,
        "coverage_ratio": coverage.coverage_ratio,
        "reason": coverage.reason,
    }
    if not coverage.passed:
        answer = insufficient_coverage_answer(coverage)
        evaluation = {"passed": False, "reason": "coverage_failed", **trace["coverage"]}
        return {"answer": answer, "citations": [], "evaluation": evaluation, "evidence": evidence}

    ollama = OllamaClient()
    answer_prompt, context = build_answer_prompt(question, evidence, [], [], trace=trace)
    generation_tokens = answer_generation_budget(question)
    trace["generation_budget"] = generation_tokens
    answer = await ollama.generate(answer_prompt, num_predict=generation_tokens)
    trace["generation_metrics"] = ollama.last_generation_metrics
    evaluation = await _judge(question, answer, context)
    scores = [
        evaluation.get(key, 0)
        for key in ("retrieval_sufficiency", "faithfulness", "citation_coverage", "completeness")
    ]
    passed = bool(scores) and min(scores) >= settings.judge_threshold
    if not passed and evaluation.get("faithfulness", 0) < settings.judge_threshold:
        answer = await ollama.generate(answer_prompt + "\n上一版回答事实一致性不足，请更保守地重写。", num_predict=generation_tokens)
        evaluation = await _judge(question, answer, context)
        scores = [
            evaluation.get(key, 0)
            for key in ("retrieval_sufficiency", "faithfulness", "citation_coverage", "completeness")
        ]
        passed = bool(scores) and min(scores) >= settings.judge_threshold
    evaluation["passed"] = passed

    answer, citations = ensure_evidence_citations(
        answer,
        evidence,
        require_sentence_citations=trace.get("prompt_compaction", {}).get("question_role") == "definition",
    )
    with SessionLocal() as session:
        session.add(Evaluation(
            question=question,
            answer=answer,
            retrieval_sufficiency=float(evaluation.get("retrieval_sufficiency", 0)),
            faithfulness=float(evaluation.get("faithfulness", 0)),
            citation_coverage=float(evaluation.get("citation_coverage", 0)),
            completeness=float(evaluation.get("completeness", 0)),
            passed=passed,
            details_json=json.dumps(evaluation, ensure_ascii=False),
        ))
        session.commit()
    return {"answer": answer, "citations": citations, "evaluation": evaluation, "evidence": evidence}


def build_answer_prompt(
    question: str,
    evidence: list[dict],
    history: list[dict],
    memories: list[str],
    trace: dict | None = None,
) -> tuple[str, str]:
    context, compact_items, role = build_compact_context(question, evidence, trace=trace)
    history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history[-12:]) or "无"
    memory_text = "\n".join(f"- {item}" for item in memories) or "无"
    contract = role_answer_contract(role, question)
    aspect_bindings: list[str] = []
    aspect_names = {
        name
        for item in compact_items
        for name, score in (item.get("aspect_scores") or {}).items()
        if float(score) >= 0.28
    }
    for name in sorted(aspect_names):
        ranked = sorted(
            (
                (float(item.get("aspect_scores", {}).get(name, 0.0)), int(item.get("chunk_id")))
                for item in compact_items
                if float(item.get("aspect_scores", {}).get(name, 0.0)) >= 0.28
            ),
            reverse=True,
        )
        if ranked:
            aspect_bindings.append(f"{name}=>[E:{ranked[0][1]}]")
    binding_rule = (
        "方面与首选证据对应：" + "；".join(aspect_bindings) + "。陈述该方面时优先引用对应证据。"
        if aspect_bindings else ""
    )
    prompt = f"""你是本地 Simulink / AUTOSAR / Stateflow 知识助手。用户用中文提问时，除英文技术名词、函数名和产品名外，必须用中文回答。
硬性规则：
1. 技术事实只能来自下面的证据。
2. 每一句事实性结论末尾都必须带 [E:数字] 引用。
3. 证据不足时直接说明，不要编造。
4. 不要把目录、页眉页脚、版权页当成主要事实依据。
5. {contract}
6. 不要输出英文段落标题，例如 Core Relationship、Mapping/Integration；中文问题请使用中文标题或不要使用标题。
7. 不要复制证据原文作为答案尾巴；只输出你整理后的最终回答。
8. 只能引用本轮给出的证据块编号：{", ".join(str(item.get("chunk_id")) for item in evidence)}。不要输出页码样式引用，例如 [E:16-2]。
9. 回答步骤题时，不要脑补按钮、菜单、弹窗或具体 UI 操作；只有证据明确写到时才可以描述。证据只支持概念流程时，就回答概念步骤。
10. {binding_rule}

用户偏好与项目上下文（不是技术证据）：
{memory_text}

最近对话：
{history_text}

当前问题：
{question}

证据：
{context}
""".strip()
    return prompt, context


async def judge_answer(question: str, answer: str, context: str) -> dict:
    return await _judge(question, answer, context)


async def _judge(question: str, answer: str, context: str) -> dict:
    settings = get_settings()
    if not settings.llm_judge_enabled:
        has_context = bool(context.strip())
        citations = re.findall(r"\[E:\d+\]", answer)
        refuses = "证据不足" in answer or "无法可靠回答" in answer
        citation_coverage = 1.0 if citations else 0.0
        retrieval_sufficiency = 0.0 if refuses else (1.0 if has_context else 0.0)
        faithfulness = 0.75 if citations else 0.35
        completeness = 0.75 if len(answer.strip()) >= 80 else 0.45
        passed = (
            retrieval_sufficiency >= settings.judge_threshold
            and citation_coverage >= settings.judge_threshold
            and completeness >= settings.judge_threshold
        )
        return {
            "retrieval_sufficiency": retrieval_sufficiency,
            "faithfulness": faithfulness,
            "citation_coverage": citation_coverage,
            "completeness": completeness,
            "passed": passed,
            "mode": "lightweight",
            "reason": "轻量规则评估：用于本地 Demo 提速；未调用 LLM Judge。",
        }

    prompt = f"""你是 RAG 评估器。请分别给 0 到 1 分，只输出 JSON。
字段：retrieval_sufficiency, faithfulness, citation_coverage, completeness, reason。
问题：{question}
回答：{answer}
证据：{context}"""
    data = await OllamaClient().generate_json(prompt)
    for key in ("retrieval_sufficiency", "faithfulness", "citation_coverage", "completeness"):
        try:
            data[key] = max(0.0, min(1.0, float(data.get(key, 0))))
        except (TypeError, ValueError):
            data[key] = 0.0
    return data
