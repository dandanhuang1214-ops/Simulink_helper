from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from slugify import slugify

from app.database import EvidenceChunk, GraphEntity, GraphRelation, SessionLocal, WikiPage
from app.services.ollama import OllamaClient
from app.services.wiki import _citation_ids, _links


ENTITY_TYPES = {"product", "concept", "method", "component", "workflow", "document", "tool", "other"}
RELATION_TYPES = {"relates_to", "part_of", "used_for", "affects", "requires", "contrasts_with"}
STOP_LABELS = {
    "a", "an", "and", "as", "by", "for", "from", "if", "in", "into", "of", "on", "or", "the", "this", "to", "with",
    "page", "pages", "source", "evidence", "summary", "note", "notes", "overview",
    "证据摘要", "编译说明", "来源章节", "核心结论",
}
TECH_SINGLE_WORDS = {
    "simulink", "stateflow", "autosar", "matlab", "solver", "model", "chart", "block", "signal", "bus",
    "composition", "component", "runnable", "interface", "calibration", "simulation", "coverage",
}
STOP_LABELS.update({
    "introduction", "layout", "system", "systems", "example", "examples", "chapter", "section", "appendix",
    "contents", "index", "getting started guide", "user guide", "getting started guide r2026a",
    "create", "validate", "open", "close", "change", "set", "run", "view", "show", "find", "select", "design",
    "\u6838\u5fc3\u7ed3\u8bba\u4e0e\u5173\u952e\u6b65\u9aa4",
    "\u4e0e Simulink/AUTOSAR \u7684\u5173\u7cfb",
    "Simulink/AUTOSAR \u5173\u7cfb\u8bf4\u660e",
    "\u7f16\u8bd1\u89c4\u5219",
    "\u76f8\u5173\u6982\u5ff5",
    "\u6ce8\u610f\u4e8b\u9879",
})
TEMPLATE_LABEL_FRAGMENTS = [
    "\u6838\u5fc3\u7ed3\u8bba",
    "\u5173\u952e\u6b65\u9aa4",
    "\u7f16\u8bd1\u89c4\u5219",
    "\u76f8\u5173\u6982\u5ff5",
    "\u6ce8\u610f\u4e8b\u9879",
    "\u7684\u5173\u7cfb",
    "\u5173\u7cfb\u8bf4\u660e",
    "\u8bc1\u636e\u6458\u8981",
    "\u7f16\u8bd1\u8bf4\u660e",
]
TECH_SINGLE_WORDS.update({"arxml", "coder", "dictionary"})
GENERIC_PATTERNS = [
    re.compile(r"^pages?\s+\d+(\s*[-–]\s*\d+)?$", re.IGNORECASE),
    re.compile(r"^introduction\s*/", re.IGNORECASE),
    re.compile(r"^mathworks[\w\s-]*r20\d{2}[ab]?$", re.IGNORECASE),
    re.compile(r"^.*\b(user guide|getting started guide)\s+r20\d{2}[ab]?$", re.IGNORECASE),
]
MOJIBAKE_RE = re.compile(r"[ÃÂ�æçåèéäöü]|(?:\\u00[0-9a-f]{2})", re.IGNORECASE)
CANONICAL_LABELS = {
    "automotive open system architecture": "AUTOSAR",
    "simulink/autosar": "Simulink AUTOSAR integration",
    "autosar components": "AUTOSAR Component",
    "autosar component creation": "AUTOSAR Component",
    "autosar software architecture modeling": "AUTOSAR Software Architecture",
    "autosar code generation": "AUTOSAR Code Generation",
    "autosar dictionary": "AUTOSAR Dictionary",
    "simple simulink model": "Simulink Model",
    "navigate a simulink model": "Simulink Model Navigation",
    "modeling in simulink": "Simulink Modeling",
    "modeling": "Model-Based Design",
    "model": "Simulink Model",
    "simulation": "Simulation",
    "embedded coder": "Embedded Coder",
    "classic platform": "AUTOSAR Classic Platform",
    "simple simulink model / create a simple model": "Simulink Model",
    "matlab/simulink": "MATLAB Simulink",
}


@dataclass
class EntityCandidate:
    label: str
    entity_type: str = "concept"
    source: str = "rule"
    confidence: float = 0.75


@dataclass
class RelationCandidate:
    source: str
    target: str
    relation_type: str = "relates_to"
    label: str = "relates_to"
    confidence: float = 0.65


def entity_key(label: str) -> str:
    normalized = re.sub(r"\s+", " ", _canonical_label(label).strip())
    value = slugify(normalized.lower())
    if value:
        return value[:280]
    return f"entity-{normalized.encode('utf-8').hex()[:240]}"


def _clean_label(value: str) -> str:
    value = re.sub(r"\[E:\d+\]", "", value or "")
    value = re.sub(r"[#*_`>]+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n:：,，.。;；")
    return value[:160]


def _canonical_label(value: str) -> str:
    value = _clean_label(value)
    compact = re.sub(r"\s+", " ", value).strip()
    lower = compact.lower()
    if lower in CANONICAL_LABELS:
        return CANONICAL_LABELS[lower]
    if lower.startswith("mathworks_simulink_getting_started") or lower.startswith("mathworkssimulinkgettingstarted"):
        return "Simulink Getting Started"
    if lower.startswith("mathworks_autosar_blockset") or lower.startswith("autosar blockset user guide"):
        return "AUTOSAR Blockset"
    if lower.startswith("overview of autosar support"):
        return "AUTOSAR Support"
    return compact


def _valid_label(value: str) -> bool:
    value = _canonical_label(value)
    if not value or len(value) < 2:
        return False
    lower = value.strip().lower()
    if lower in STOP_LABELS:
        return False
    if any(fragment in value for fragment in TEMPLATE_LABEL_FRAGMENTS):
        return False
    if MOJIBAKE_RE.search(value):
        return False
    if any(pattern.match(value) for pattern in GENERIC_PATTERNS):
        return False
    if "/" in value and not any(token in lower for token in ["autosar", "simulink", "stateflow"]):
        return False
    if len(value.split()) == 1 and lower not in TECH_SINGLE_WORDS and len(value) < 4:
        return False
    if any(token in value for token in ["证据摘要", "编译说明", "来源章节"]):
        return False
    if re.fullmatch(r"E\s*:\s*\d+", value, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"\d+(\.\d+)*", value):
        return False
    return True


def _guess_type(label: str) -> str:
    lower = label.lower()
    if any(token in lower for token in ["simulink", "stateflow", "autosar", "matlab"]):
        return "product"
    if any(token in lower for token in ["solver", "block", "chart", "composition", "component", "model"]):
        return "component"
    if any(token in lower for token in ["import", "export", "generate", "test", "simulation", "workflow"]):
        return "workflow"
    if any(token in lower for token in ["method", "strategy", "algorithm", "coverage"]):
        return "method"
    return "concept"


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"entities": [], "relations": []}
    if not isinstance(parsed, dict):
        return {"entities": [], "relations": []}
    return parsed


def _rule_entities(page: WikiPage) -> list[EntityCandidate]:
    candidates: list[EntityCandidate] = []
    title_parts = [part.strip() for part in re.split(r"\s*/\s*", page.title or "") if part.strip()]
    for value in [*title_parts[:3], *_links(page.content)]:
        label = _canonical_label(value)
        if _valid_label(label):
            candidates.append(EntityCandidate(label=label, entity_type=_guess_type(label), source="wiki_link", confidence=0.9))

    headings = re.findall(r"^#{1,4}\s+(.+)$", page.content or "", flags=re.MULTILINE)
    for heading in headings[:16]:
        label = _canonical_label(heading)
        if _valid_label(label):
            candidates.append(EntityCandidate(label=label, entity_type=_guess_type(label), source="heading", confidence=0.78))

    # English technical noun phrases are useful for Simulink/AUTOSAR docs.
    phrases = re.findall(r"\b[A-Z][A-Za-z0-9/+.-]*(?:\s+[A-Z][A-Za-z0-9/+.-]*){0,4}\b", page.content or "")
    stop = {"The", "This", "For", "Source", "Page", "Pages", "Evidence", "MATLAB", "To", "In", "On", "Of"}
    for phrase, count in Counter(phrases).most_common(18):
        label = _canonical_label(phrase)
        if count >= 1 and label not in stop and _valid_label(label):
            candidates.append(EntityCandidate(label=label, entity_type=_guess_type(label), source="phrase", confidence=0.68))

    seen: set[str] = set()
    deduped: list[EntityCandidate] = []
    for item in candidates:
        key = entity_key(item.label)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:18]


async def _llm_extract(page: WikiPage, rule_entities: list[EntityCandidate]) -> tuple[list[EntityCandidate], list[RelationCandidate]]:
    seed = ", ".join(item.label for item in rule_entities[:12])
    content = re.sub(r"\s+", " ", page.content or "")[:4500]
    prompt = f"""你是本地 LLM-Wiki 图谱编译器。请只基于给定 Wiki 页面抽取实体和关系。
输出必须是严格 JSON，不要 Markdown。
实体类型只能从 product, concept, method, component, workflow, document, tool, other 中选择。
关系类型只能从 relates_to, part_of, used_for, affects, requires, contrasts_with 中选择。
最多 10 个 entities，最多 8 个 relations。不要把 [E:n] 当实体。

已知候选实体：{seed}
Wiki 标题：{page.title}
Wiki 内容：{content}

JSON 格式：
{{"entities":[{{"label":"Simulink","type":"product","confidence":0.9}}],
 "relations":[{{"source":"AUTOSAR Composition","target":"Simulink Model","type":"used_for","label":"imports to","confidence":0.75}}]}}
"""
    try:
        raw = await OllamaClient().generate(prompt, num_predict=650)
    except Exception:
        return [], []
    parsed = _parse_json_object(raw)
    entities: list[EntityCandidate] = []
    relations: list[RelationCandidate] = []
    for item in parsed.get("entities", []):
        if not isinstance(item, dict):
            continue
        label = _canonical_label(str(item.get("label", "")))
        if not _valid_label(label):
            continue
        item_type = str(item.get("type", "concept"))
        confidence = float(item.get("confidence", 0.72) or 0.72)
        entities.append(EntityCandidate(label, item_type if item_type in ENTITY_TYPES else "concept", "llm", min(max(confidence, 0), 1)))
    for item in parsed.get("relations", []):
        if not isinstance(item, dict):
            continue
        source = _canonical_label(str(item.get("source", "")))
        target = _canonical_label(str(item.get("target", "")))
        if not _valid_label(source) or not _valid_label(target) or entity_key(source) == entity_key(target):
            continue
        relation_type = str(item.get("type", "relates_to"))
        confidence = float(item.get("confidence", 0.65) or 0.65)
        relations.append(RelationCandidate(
            source=source,
            target=target,
            relation_type=relation_type if relation_type in RELATION_TYPES else "relates_to",
            label=_clean_label(str(item.get("label", relation_type))) or relation_type,
            confidence=min(max(confidence, 0), 1),
        ))
    return entities[:12], relations[:10]


def _merge_refs(existing: str | None, values: list[str]) -> str:
    try:
        current = json.loads(existing or "[]")
    except json.JSONDecodeError:
        current = []
    merged = sorted({str(item) for item in current + values if str(item)})
    return json.dumps(merged, ensure_ascii=False)


async def compile_knowledge_graph(use_llm: bool = True, limit_pages: int = 80) -> dict[str, Any]:
    with SessionLocal() as session:
        pages = session.query(WikiPage).order_by(WikiPage.updated_at.desc()).limit(limit_pages).all()

    page_entities: dict[str, list[EntityCandidate]] = {}
    page_relations: dict[str, list[RelationCandidate]] = {}
    page_citations: dict[str, list[int]] = {}

    for page in pages:
        rule_items = _rule_entities(page)
        llm_entities: list[EntityCandidate] = []
        llm_relations: list[RelationCandidate] = []
        if use_llm:
            llm_entities, llm_relations = await _llm_extract(page, rule_items)
        combined = {entity_key(item.label): item for item in rule_items}
        for item in llm_entities:
            key = entity_key(item.label)
            if key not in combined or item.confidence > combined[key].confidence:
                combined[key] = item
        page_entities[page.slug] = list(combined.values())[:24]
        page_relations[page.slug] = llm_relations
        page_citations[page.slug] = sorted(_citation_ids(page.content))

    mention_counter: Counter[str] = Counter()
    citation_counter: defaultdict[str, set[int]] = defaultdict(set)
    for slug, entities in page_entities.items():
        for item in entities:
            key = entity_key(item.label)
            mention_counter[key] += 1
            citation_counter[key].update(page_citations.get(slug, []))

    with SessionLocal() as session:
        session.query(GraphRelation).delete()
        session.query(GraphEntity).delete()
        session.commit()

        entity_rows: dict[str, GraphEntity] = {}
        for slug, entities in page_entities.items():
            for item in entities:
                key = entity_key(item.label)
                row = entity_rows.get(key)
                if not row:
                    row = GraphEntity(
                        entity_key=key,
                        label=item.label,
                        entity_type=item.entity_type if item.entity_type in ENTITY_TYPES else _guess_type(item.label),
                        aliases_json="[]",
                        source=item.source,
                        confidence=item.confidence,
                        evidence_count=len(citation_counter[key]),
                        wiki_count=mention_counter[key],
                    )
                    session.add(row)
                    session.flush()
                    entity_rows[key] = row
                else:
                    row.wiki_count = mention_counter[key]
                    row.evidence_count = len(citation_counter[key])
                    row.confidence = max(row.confidence, item.confidence)

        relation_keys: set[tuple[int, int, str]] = set()
        for slug, relations in page_relations.items():
            refs = [f"wiki:{slug}"]
            evidence_refs = [f"E:{item}" for item in page_citations.get(slug, [])[:12]]
            for item in relations:
                source = entity_rows.get(entity_key(item.source))
                target = entity_rows.get(entity_key(item.target))
                if not source or not target:
                    continue
                key = (source.id, target.id, item.relation_type)
                if key in relation_keys:
                    continue
                relation_keys.add(key)
                session.add(GraphRelation(
                    source_entity_id=source.id,
                    target_entity_id=target.id,
                    relation_type=item.relation_type,
                    label=item.label,
                    evidence_refs_json=json.dumps(evidence_refs, ensure_ascii=False),
                    wiki_refs_json=json.dumps(refs, ensure_ascii=False),
                    weight=1.0,
                    confidence=item.confidence,
                ))

        # Shared entity co-occurrence gives the graph useful structure even when LLM extraction is conservative.
        for slug, entities in page_entities.items():
            strong = [entity_rows[entity_key(item.label)] for item in entities[:8] if entity_key(item.label) in entity_rows]
            for index, source in enumerate(strong):
                for target in strong[index + 1:index + 4]:
                    key = (source.id, target.id, "co_occurs")
                    if key in relation_keys:
                        continue
                    relation_keys.add(key)
                    session.add(GraphRelation(
                        source_entity_id=source.id,
                        target_entity_id=target.id,
                        relation_type="co_occurs",
                        label="co_occurs",
                        evidence_refs_json=json.dumps([f"E:{item}" for item in page_citations.get(slug, [])[:6]], ensure_ascii=False),
                        wiki_refs_json=json.dumps([f"wiki:{slug}"], ensure_ascii=False),
                        weight=0.45,
                        confidence=0.55,
                    ))

        session.commit()
        entity_count = session.query(GraphEntity).count()
        relation_count = session.query(GraphRelation).count()

    return {
        "compiled": True,
        "pages": len(pages),
        "entities": entity_count,
        "relations": relation_count,
        "mode": "rules+llm" if use_llm else "rules",
    }
