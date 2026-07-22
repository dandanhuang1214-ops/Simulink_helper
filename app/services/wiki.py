from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from slugify import slugify

from app.config import get_settings
from app.database import Document, EvidenceChunk, GraphEntity, GraphRelation, SessionLocal, WikiPage
from app.services.ollama import OllamaClient
from app.services.storage import ensure_storage
from sqlalchemy import text


MAX_WIKI_SECTIONS = 30
MAX_EVIDENCE_PER_SECTION = 12
PAGE_WINDOW_WITHOUT_HEADINGS = 10
CITATION_RE = re.compile(r"\[E:(\d+)\]")


@dataclass
class SectionPack:
    title: str
    heading_path: str
    chunks: list[EvidenceChunk]
    index: int


def _citation_ids(content: str) -> set[int]:
    return {int(value) for value in CITATION_RE.findall(content or "")}


def _normalize_citations(content: str) -> str:
    content = re.sub(r"\[\[\s*E\s*:\s*(\d+)\s*\]\]", r"[E:\1]", content or "")
    content = re.sub(r"\[\s*E\s*:\s*(\d+)\s*\]", r"[E:\1]", content)
    return content


def _sample_chunks(chunks: list[EvidenceChunk], limit: int = MAX_EVIDENCE_PER_SECTION) -> list[EvidenceChunk]:
    if len(chunks) <= limit:
        return chunks
    step = (len(chunks) - 1) / max(1, limit - 1)
    indexes = sorted({round(i * step) for i in range(limit)})
    return [chunks[index] for index in indexes]


def _short_text(value: str, limit: int = 360) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip() + ("..." if len(value) > limit else "")


def _key_from_heading(chunk: EvidenceChunk, depth: int) -> str:
    parts = [part.strip() for part in (chunk.heading_path or "").split("/") if part.strip()]
    if not parts:
        return ""
    return " / ".join(parts[:depth])


def _group_by_key(chunks: list[EvidenceChunk], depth: int) -> list[SectionPack]:
    groups: list[SectionPack] = []
    current_key = ""
    current: list[EvidenceChunk] = []

    def flush() -> None:
        nonlocal current, current_key
        if not current:
            return
        title = current_key or _page_window_title(current)
        groups.append(SectionPack(title=title, heading_path=current_key, chunks=current, index=len(groups) + 1))
        current = []

    for chunk in chunks:
        key = _key_from_heading(chunk, depth)
        if current and key != current_key:
            flush()
        current_key = key
        current.append(chunk)
    flush()
    return groups


def _page_window_title(chunks: list[EvidenceChunk]) -> str:
    pages = [chunk.page for chunk in chunks if chunk.page]
    if not pages:
        return f"Evidence {chunks[0].ordinal + 1}-{chunks[-1].ordinal + 1}"
    start, end = min(pages), max(pages)
    return f"Pages {start}-{end}" if start != end else f"Page {start}"


def _group_without_headings(chunks: list[EvidenceChunk]) -> list[SectionPack]:
    groups: list[SectionPack] = []
    current: list[EvidenceChunk] = []
    window_start: int | None = None
    for chunk in chunks:
        page = chunk.page or 0
        if window_start is None:
            window_start = page
        if current and page and window_start and page >= window_start + PAGE_WINDOW_WITHOUT_HEADINGS:
            groups.append(SectionPack(_page_window_title(current), "", current, len(groups) + 1))
            current = []
            window_start = page
        current.append(chunk)
    if current:
        groups.append(SectionPack(_page_window_title(current), "", current, len(groups) + 1))
    return groups


def _section_packs(chunks: list[EvidenceChunk]) -> list[SectionPack]:
    ordered = sorted(chunks, key=lambda item: item.ordinal)
    if not ordered:
        return []
    if any(chunk.heading_path for chunk in ordered):
        groups = _group_by_key(ordered, depth=2)
        if len(groups) > MAX_WIKI_SECTIONS:
            groups = _group_by_key(ordered, depth=1)
    else:
        groups = _group_without_headings(ordered)

    if len(groups) <= MAX_WIKI_SECTIONS:
        return groups

    merged: list[SectionPack] = []
    ratio = math.ceil(len(groups) / MAX_WIKI_SECTIONS)
    for start in range(0, len(groups), ratio):
        bundle = groups[start:start + ratio]
        bundle_chunks = [chunk for group in bundle for chunk in group.chunks]
        title = f"{bundle[0].title} ~ {bundle[-1].title}" if len(bundle) > 1 else bundle[0].title
        merged.append(SectionPack(title, bundle[0].heading_path, bundle_chunks, len(merged) + 1))
    return merged


def _evidence_prompt(chunks: list[EvidenceChunk]) -> str:
    return "\n\n".join(
        f"[E:{chunk.id}] page={chunk.page or 'unknown'} type={chunk.block_type}\n{_short_text(chunk.content, 900)}"
        for chunk in chunks
    )


def _fallback_section_content(document: Document, section: SectionPack, evidence: list[EvidenceChunk], reason: str) -> str:
    lines = [
        f"# {document.title} / {section.title}",
        "",
        "> 该页由确定性编译器生成，因为本地模型输出未通过引用校验。",
        f"> 原因：{reason}",
        "",
        "## 证据摘要",
        "",
    ]
    for chunk in evidence:
        location = f"p.{chunk.page}" if chunk.page else "page unknown"
        lines.append(f"- {location}: {_short_text(chunk.content)} [E:{chunk.id}]")
    lines.extend([
        "",
        "## 编译说明",
        "",
        "- 本页只使用列出的原始证据块。",
        "- 如需更精细的概念页，可在审核后从本章节继续生成 claim 与关系。",
    ])
    return "\n".join(lines)


async def _compile_section_content(document: Document, section: SectionPack) -> str:
    evidence = _sample_chunks(section.chunks)
    allowed_ids = {chunk.id for chunk in evidence}
    prompt = f"""你是本地 LLM-Wiki 编译器。请只依据证据为资料生成中文 Markdown 章节页。

硬规则：
1. 每一个事实性句子必须带 [E:数字] 引用。
2. 只能使用下方证据里出现的引用编号。
3. 不要编造证据中没有的信息。
4. 优先写：核心结论、关键步骤、术语、注意事项、与 Simulink/AUTOSAR 的关系。
5. 可以用 [[概念名]] 标出重要概念链接。

文档：{document.title}
章节：{section.title}
证据：
{_evidence_prompt(evidence)}
"""
    try:
        content = _normalize_citations((await OllamaClient().generate(prompt, num_predict=900)).strip())
    except Exception as exc:
        content = ""
        reason = f"LLM 编译失败：{type(exc).__name__}: {exc}"
    else:
        reason = "模型输出缺少合法引用"

    citations = _citation_ids(content)
    if not content or not citations or not citations.issubset(allowed_ids):
        ensure_storage().joinpath(
            "error-book", f"wiki-citation-fallback-doc-{document.id}-section-{section.index}.log"
        ).write_text(
            json.dumps({
                "document_id": document.id,
                "section": section.title,
                "reason": reason,
                "allowed_ids": sorted(allowed_ids),
                "model_citations": sorted(citations),
                "model_preview": content[:1200],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return _fallback_section_content(document, section, evidence, reason)
    return content


def _section_slug(document: Document, section: SectionPack) -> str:
    base = slugify(document.title) or "document"
    title = slugify(section.title) or f"section-{section.index}"
    value = f"source-{document.id}-{base}-s{section.index}-{title}"
    return value[:280].rstrip("-")


def _write_draft(slug: str, content: str) -> None:
    draft_path = ensure_storage() / "drafts" / f"{slug}.md"
    draft_path.write_text(content, encoding="utf-8")


def _links(content: str) -> list[str]:
    values = []
    for value in re.findall(r"\[\[([^\]]+)\]\]", content or ""):
        value = value.strip()
        if re.fullmatch(r"E\s*:\s*\d+", value, flags=re.IGNORECASE):
            continue
        if value:
            values.append(value)
    return sorted(set(values))


async def compile_source_page(document: Document, chunks: list[EvidenceChunk]) -> list[WikiPage]:
    if not document.enabled:
        return []
    packs = _section_packs(chunks)
    if not packs:
        return []
    use_llm_compile = len(chunks) <= get_settings().wiki_llm_max_chunks

    root = ensure_storage()
    root.joinpath("drafts").mkdir(parents=True, exist_ok=True)
    root.joinpath("error-book").mkdir(parents=True, exist_ok=True)

    section_pages: list[tuple[SectionPack, str, str, list[str]]] = []
    for pack in packs:
        if use_llm_compile:
            content = await _compile_section_content(document, pack)
        else:
            evidence = _sample_chunks(pack.chunks)
            content = _fallback_section_content(
                document,
                pack,
                evidence,
                f"大文档包含 {len(chunks)} 个证据块，Demo 阶段跳过逐章节 LLM 编译以提升入库速度。",
            )
        slug = _section_slug(document, pack)
        section_pages.append((pack, slug, content, _links(content)))
        _write_draft(slug, content)

    index_slug = f"source-{document.id}-{slugify(document.title) or 'document'}"
    index_lines = [
        f"# {document.title}",
        "",
        "## 来源章节",
        "",
    ]
    index_links: list[str] = []
    for pack, slug, _content, _page_links in section_pages:
        sampled = _sample_chunks(pack.chunks, 1)
        citation = f" [E:{sampled[0].id}]" if sampled else ""
        index_lines.append(f"- [[{pack.title}]]：{len(pack.chunks)} 个证据块，页面 `{slug}`{citation}")
        index_links.append(pack.title)
    index_lines.extend([
        "",
        "## 编译规则",
        "",
        "- 原始文件不可变保存；本页与章节页均可从 evidence chunks 重建。",
        "- 章节页必须包含原始证据引用；模型输出无引用时自动降级为证据摘要页。",
    ])
    index_content = "\n".join(index_lines)
    _write_draft(index_slug, index_content)

    with SessionLocal() as session:
        session.query(WikiPage).filter_by(source_document_id=document.id).delete()
        index_page = WikiPage(
            slug=index_slug,
            title=document.title,
            content=index_content,
            page_type="source",
            source_document_id=document.id,
            links_json=json.dumps(index_links, ensure_ascii=False),
        )
        session.add(index_page)
        for pack, slug, content, links in section_pages:
            page = WikiPage(
                slug=slug,
                title=f"{document.title} / {pack.title}",
                content=content,
                page_type="source_section",
                source_document_id=document.id,
                links_json=json.dumps(links, ensure_ascii=False),
            )
            session.add(page)
        session.commit()
        pages = session.query(WikiPage).filter_by(source_document_id=document.id).order_by(WikiPage.id).all()
        session.execute(text("DELETE FROM wiki_fts"))
        for page in session.query(WikiPage).order_by(WikiPage.id).all():
            session.execute(
                text("INSERT INTO wiki_fts(page_id,title,content) VALUES(:id,:title,:content)"),
                {"id": page.id, "title": page.title, "content": page.content},
            )
        session.commit()
        return pages


def _safe_json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def wiki_graph() -> dict:
    """Build a lightweight graph from stable, already-auditable entities.

    V1 deliberately keeps the graph small:
    - documents are source nodes;
    - wiki pages are derived nodes;
    - concepts come from explicit wiki links;
    - evidence nodes are only chunks cited by wiki pages.

    This gives the frontend a useful graph now, while leaving room for later
    claim/relation extraction without changing the public response shape.
    """
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def add_node(node_id: str, **payload: object) -> None:
        if node_id in nodes:
            nodes[node_id].update({key: value for key, value in payload.items() if value is not None})
            return
        nodes[node_id] = {"id": node_id, **payload}

    def add_edge(source: str, target: str, label: str, **payload: object) -> None:
        key = (source, target, label)
        if key not in edges:
            edges[key] = {"source": source, "target": target, "label": label, **payload}

    with SessionLocal() as session:
        documents = session.query(Document).filter(
            Document.enabled.is_(True),
            Document.status == "ready",
        ).order_by(Document.id).all()
        enabled_document_ids = {document.id for document in documents}
        pages = session.query(WikiPage).filter(
            (WikiPage.source_document_id.is_(None))
            | (WikiPage.source_document_id.in_(enabled_document_ids))
        ).order_by(WikiPage.id).all()
        graph_entities = session.query(GraphEntity).order_by(GraphEntity.id).all()
        graph_relations = session.query(GraphRelation).order_by(GraphRelation.id).all()

        for document in documents:
            add_node(
                f"doc:{document.id}",
                type="document",
                label=document.title,
                status=document.status,
                document_id=document.id,
                filename=document.filename,
                release=document.release,
            )

        known_titles = {page.title: page for page in pages}
        cited_ids: set[int] = set()

        for page in pages:
            page_id = f"wiki:{page.slug}"
            add_node(
                page_id,
                type="wiki_page",
                label=page.title,
                status=page.status,
                page_type=page.page_type,
                slug=page.slug,
                source_document_id=page.source_document_id,
            )
            if page.source_document_id:
                doc_id = f"doc:{page.source_document_id}"
                add_edge(doc_id, page_id, "compiled_to")

            for citation_id in sorted(_citation_ids(page.content)):
                cited_ids.add(citation_id)
                evidence_id = f"evidence:{citation_id}"
                add_node(evidence_id, type="evidence", label=f"E:{citation_id}", chunk_id=citation_id)
                add_edge(page_id, evidence_id, "cites")

            for concept in _safe_json_list(page.links_json) or _links(page.content):
                if re.fullmatch(r"E\s*:\s*\d+", concept, flags=re.IGNORECASE):
                    continue
                target_page = known_titles.get(concept)
                if target_page:
                    target_id = f"wiki:{target_page.slug}"
                    add_edge(page_id, target_id, "links_to")
                    continue
                if not graph_entities:
                    concept_id = f"concept:{slugify(concept) or slugify(concept.encode('utf-8').hex())}"
                    add_node(concept_id, type="concept", label=concept, status="derived")
                    add_edge(page_id, concept_id, "mentions")

        entity_by_id: dict[int, GraphEntity] = {}
        for entity in graph_entities:
            entity_by_id[entity.id] = entity
            entity_id = f"entity:{entity.id}"
            add_node(
                entity_id,
                type="entity",
                label=entity.label,
                status="compiled",
                entity_type=entity.entity_type,
                entity_key=entity.entity_key,
                confidence=entity.confidence,
                evidence_count=entity.evidence_count,
                wiki_count=entity.wiki_count,
            )

        if graph_entities:
            top_entities = sorted(graph_entities, key=lambda item: (item.wiki_count, item.evidence_count, item.confidence), reverse=True)
            for page in pages:
                haystack = f"{page.title}\n{page.content}".lower()
                page_id = f"wiki:{page.slug}"
                linked = 0
                for entity in top_entities:
                    if linked >= 18:
                        break
                    label = entity.label.lower()
                    if len(label) < 2:
                        continue
                    if label in haystack:
                        add_edge(page_id, f"entity:{entity.id}", "mentions", confidence=entity.confidence)
                        linked += 1

        for relation in graph_relations:
            source = entity_by_id.get(relation.source_entity_id)
            target = entity_by_id.get(relation.target_entity_id)
            if not source or not target:
                continue
            add_edge(
                f"entity:{source.id}",
                f"entity:{target.id}",
                relation.relation_type,
                relation_label=relation.label,
                weight=relation.weight,
                confidence=relation.confidence,
                wiki_refs=json.loads(relation.wiki_refs_json or "[]"),
                evidence_refs=json.loads(relation.evidence_refs_json or "[]"),
            )

        if cited_ids:
            chunks = session.query(EvidenceChunk).filter(EvidenceChunk.id.in_(sorted(cited_ids))).all()
            cited_chunk_ids = {int(item.id) for item in chunks}
            for chunk in chunks:
                evidence_id = f"evidence:{chunk.id}"
                document = chunk.document
                add_node(
                    evidence_id,
                    type="evidence",
                    label=f"E:{chunk.id}",
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_title=document.title if document else None,
                    page=chunk.page,
                    block_type=chunk.block_type,
                    heading_path=chunk.heading_path,
                )
                add_edge(evidence_id, f"doc:{chunk.document_id}", "from_document")
                if chunk.next_id and int(chunk.next_id) in cited_chunk_ids:
                    add_edge(evidence_id, f"evidence:{int(chunk.next_id)}", "next_chunk")

    stats = {
        "documents": sum(1 for node in nodes.values() if node.get("type") == "document"),
        "wiki_pages": sum(1 for node in nodes.values() if node.get("type") == "wiki_page"),
        "concepts": sum(1 for node in nodes.values() if node.get("type") == "concept"),
        "entities": sum(1 for node in nodes.values() if node.get("type") == "entity"),
        "evidence": sum(1 for node in nodes.values() if node.get("type") == "evidence"),
        "edges": len(edges),
    }
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "stats": stats}


def publish_page(page: WikiPage) -> None:
    root = ensure_storage()
    target = root / "wiki" / f"{page.slug}.md"
    target.write_text(page.content, encoding="utf-8")
