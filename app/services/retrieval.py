from __future__ import annotations

import re
from collections import defaultdict
from time import perf_counter

from qdrant_client import QdrantClient
from sqlalchemy import text

from app.config import get_settings
from app.database import Document, EvidenceChunk, SessionLocal, WikiPage
from app.services.domains import document_domains, domain_boost, preferred_domains
from app.services.graph_retrieval import graph_candidate_scores
from app.services.ollama import OllamaClient
from app.services.text import lexical_tokens


WIKI_CITATION_RE = re.compile(r"\[E:(\d+)\]")


DOMAIN_QUERY_EXPANSIONS = [
    (("stateflow", "状态机", "状态流"), "Stateflow chart finite state machine state transition event action Simulink"),
    (("chart", "图表", "状态图"), "Stateflow chart state transition diagram"),
    (("transition", "转移", "转换"), "transition state event condition action Stateflow"),
    (("event", "事件"), "event broadcast chart execution Stateflow"),
    (("truth table", "真值表"), "truth table decision logic Stateflow"),
    (("autosar", "arxml"), "AUTOSAR component composition ARXML Simulink"),
    (("simulink", "仿真"), "Simulink model simulation block signal subsystem"),
    (("求解器", "solver", "步长"), "solver fixed-step variable-step simulation Simulink"),
]


def _fts_query(value: str) -> str:
    terms = lexical_tokens(value)
    return " OR ".join(f'"{term}"' for term in terms[:12])


def _domain_expanded_queries(query: str) -> list[str]:
    lowered = query.lower()
    values = [query]
    for triggers, expansion in DOMAIN_QUERY_EXPANSIONS:
        if any(trigger.lower() in lowered for trigger in triggers):
            values.append(f"{query} {expansion}")
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _is_simple_relation_query(query: str) -> bool:
    lowered = query.lower()
    simple_relation = any(cue in lowered for cue in ("关系", "是什么", "定义", "relationship", "what is"))
    procedural_or_compare = any(cue in lowered for cue in (
        "为什么", "区别", "对比", "如何", "步骤", "流程", "原因", "怎么",
        "compare", "difference", "why", "how", "steps", "workflow",
    ))
    return simple_relation and not procedural_or_compare


def _dense_skip_reason(query: str, scores: dict[int, float], limit: int) -> str | None:
    lowered = query.lower()
    has_distinctive_term = any(term in lowered for term in (
        "autosar", "arxml", "stateflow", "simulink", "solver", "fixed-step", "variable-step",
        "求解器", "固定步长", "可变步长", "状态机",
    ))
    if not has_distinctive_term:
        return None
    solver_like = any(term in lowered for term in ("solver", "fixed-step", "variable-step", "求解器", "固定步长", "可变步长"))
    minimum_lexical_hits = 4 if solver_like else min(8, limit)
    if len(scores) < minimum_lexical_hits:
        return None
    # AUTOSAR terms are distinctive enough for the lexical fast path.
    if "autosar" in lowered or "arxml" in lowered:
        return "skip:dense_fast_path_autosar"
    if solver_like:
        return "skip:dense_fast_path_solver"
    if "stateflow" in lowered or "状态机" in lowered:
        return "skip:dense_fast_path_stateflow"
    if any(term in lowered for term in ("code generation", "代码生成", "生成代码", "coder", "embedded coder")):
        return "skip:dense_fast_path_codegen"
    if _is_simple_relation_query(query):
        return "skip:dense_fast_path_simple_domain"
    if "simulink" in lowered and len(scores) >= min(10, limit):
        return "skip:dense_fast_path_simulink"
    return None


def _can_skip_dense(query: str, scores: dict[int, float], limit: int) -> bool:
    return _dense_skip_reason(query, scores, limit) is not None


def _is_likely_toc_chunk(content: str, heading_path: str, page: int | None) -> bool:
    if page and page > 35:
        return False
    lowered = content.strip().lower()
    dot_runs = content.count(". . .")
    page_refs = len(re.findall(r"\b\d+-\d+\b", content))
    on_page_refs = lowered.count(" on page ")
    if not heading_path and lowered.startswith("contents "):
        return True
    return dot_runs >= 3 or page_refs >= 8 or on_page_refs >= 5


def _concept_boost(query: str, candidate: dict) -> float:
    lowered_query = query.lower()
    wants_definition = any(cue in lowered_query for cue in ("是什么", "定义", "关系", "what is", "relationship"))
    if not wants_definition:
        return 0.0
    text_value = f"{candidate.get('heading_path') or ''} {candidate.get('content') or ''}".lower()
    boost = 0.0
    if "stateflow" in lowered_query or "状态机" in lowered_query:
        if "finite state machine" in text_value:
            boost += 0.04
        if "model a finite state machine" in text_value:
            boost += 0.03
        if "types of stateflow blocks" in text_value:
            boost += 0.02
    if "autosar" in lowered_query and ("component" in text_value or "composition" in text_value):
        boost += 0.02
    if ("solver" in lowered_query or "求解器" in lowered_query) and (
        "fixed-step" in text_value or "variable-step" in text_value
    ):
        boost += 0.02
    return boost


def _should_rerank(query: str, candidates: list[dict], final_limit: int) -> tuple[bool, str]:
    if len(candidates) <= final_limit:
        return False, "skip:not_enough_extra_candidates"
    if _is_simple_relation_query(query):
        return False, "skip:simple_relation"
    domain_candidates = [item for item in candidates if item.get("domain_boost", 0.0) > 0]
    if len(domain_candidates) >= final_limit:
        return False, "skip:domain_enough"
    tokens = lexical_tokens(query)
    lowered = query.lower()
    has_complex_cue = any(cue in lowered for cue in (
        "为什么", "区别", "对比", "如何", "步骤", "流程", "原因", "怎么",
        "compare", "difference", "why", "how", "steps", "workflow",
    ))
    if len(tokens) <= 3 and len(candidates) <= 12 and not has_complex_cue:
        return False, "skip:short_query"
    return True, "use:complex_or_many_candidates"


def _collect_bm25(
    variant: str,
    *,
    limit: int,
    allowed_documents: set[int] | None,
    scores: dict[int, float],
    channels: dict[int, set[str]],
) -> None:
    fts = _fts_query(variant)
    if not fts:
        return
    with SessionLocal() as session:
        rows = session.execute(
            text("SELECT chunk_id, bm25(evidence_fts) AS score FROM evidence_fts WHERE evidence_fts MATCH :query ORDER BY score LIMIT :limit"),
            {"query": fts, "limit": limit},
        ).all()
        if allowed_documents is not None:
            doc_rows = session.query(EvidenceChunk.id, EvidenceChunk.document_id).filter(
                EvidenceChunk.id.in_([int(row.chunk_id) for row in rows])
            ).all()
            doc_by_chunk = {int(row.id): int(row.document_id) for row in doc_rows}
        else:
            doc_by_chunk = {}
    for rank, row in enumerate(rows):
        chunk_id = int(row.chunk_id)
        if allowed_documents is not None and doc_by_chunk.get(chunk_id) not in allowed_documents:
            continue
        scores[chunk_id] += 1 / (60 + rank + 1)
        channels[chunk_id].add("bm25")


def _citation_ids(content: str) -> list[int]:
    values = [int(item) for item in WIKI_CITATION_RE.findall(content or "")]
    return list(dict.fromkeys(values))


def _collect_wiki(
    variant: str,
    *,
    limit: int,
    allowed_documents: set[int] | None,
    scores: dict[int, float],
    channels: dict[int, set[str]],
    metadata: dict[int, dict],
) -> dict:
    fts = _fts_query(variant)
    if not fts:
        return {"pages": [], "evidence_count": 0}
    with SessionLocal() as session:
        rows = session.execute(
            text("SELECT page_id, bm25(wiki_fts) AS score FROM wiki_fts WHERE wiki_fts MATCH :query ORDER BY score LIMIT :limit"),
            {"query": fts, "limit": limit},
        ).all()
        page_ids = [int(row.page_id) for row in rows]
        pages = {
            int(page.id): page
            for page in session.query(WikiPage).filter(WikiPage.id.in_(page_ids)).all()
        } if page_ids else {}
        cited_ids: list[int] = []
        page_refs_by_chunk: dict[int, list[dict]] = defaultdict(list)
        for rank, row in enumerate(rows):
            page = pages.get(int(row.page_id))
            if not page:
                continue
            for local_rank, chunk_id in enumerate(_citation_ids(page.content)[:12]):
                cited_ids.append(chunk_id)
                page_refs_by_chunk[chunk_id].append({
                    "slug": page.slug,
                    "title": page.title,
                    "rank": rank + 1,
                    "local_rank": local_rank + 1,
                })
        if not cited_ids:
            return {
                "pages": [{"page_id": int(row.page_id), "rank": index + 1} for index, row in enumerate(rows)],
                "evidence_count": 0,
            }
        doc_rows = session.query(EvidenceChunk.id, EvidenceChunk.document_id).filter(EvidenceChunk.id.in_(cited_ids)).all()
        doc_by_chunk = {int(row.id): int(row.document_id) for row in doc_rows}

    evidence_count = 0
    for chunk_id in dict.fromkeys(cited_ids):
        if allowed_documents is not None and doc_by_chunk.get(chunk_id) not in allowed_documents:
            continue
        refs = page_refs_by_chunk.get(chunk_id, [])
        if not refs:
            continue
        best_page_rank = min(ref["rank"] for ref in refs)
        best_local_rank = min(ref["local_rank"] for ref in refs)
        scores[chunk_id] += 1 / (70 + best_page_rank + best_local_rank)
        channels[chunk_id].add("wiki")
        metadata[chunk_id].setdefault("wiki_refs", [])
        existing = {
            (item.get("slug"), item.get("title"))
            for item in metadata[chunk_id]["wiki_refs"]
        }
        for ref in refs[:3]:
            key = (ref["slug"], ref["title"])
            if key not in existing:
                metadata[chunk_id]["wiki_refs"].append(ref)
                existing.add(key)
        evidence_count += 1
    return {
        "pages": [
            {
                "page_id": int(row.page_id),
                "title": pages.get(int(row.page_id)).title if pages.get(int(row.page_id)) else None,
                "slug": pages.get(int(row.page_id)).slug if pages.get(int(row.page_id)) else None,
                "rank": index + 1,
            }
            for index, row in enumerate(rows)
        ],
        "evidence_count": evidence_count,
    }


async def rewrite_query(query: str) -> list[str]:
    prompt = f"""将问题改写成最多两个用于知识库检索的短查询。保留原有技术名词、版本号和函数名。只输出 JSON：{{"queries":["...","..."]}}
原问题：{query}"""
    data = await OllamaClient().generate_json(prompt)
    values = [item.strip() for item in data.get("queries", []) if isinstance(item, str) and item.strip()]
    return [query, *values[:2]]


async def hybrid_search(
    query: str,
    limit: int = 10,
    use_rewrite: bool = True,
    use_rerank: bool = True,
    retrieval_profile: str | None = None,
    document_ids: list[int] | None = None,
    releases: list[str] | None = None,
    trace: dict | None = None,
) -> list[dict]:
    started = perf_counter()
    settings = get_settings()
    profile = (retrieval_profile or settings.retrieval_profile or "fast").lower()
    query_domains = preferred_domains(query)
    allowed_documents: set[int] | None = None
    if document_ids or releases:
        with SessionLocal() as session:
            documents = session.query(Document.id)
            if document_ids:
                documents = documents.filter(Document.id.in_(document_ids))
            if releases:
                documents = documents.filter(Document.release.in_(releases))
            allowed_documents = {int(row.id) for row in documents.all()}

    queries = await rewrite_query(query) if use_rewrite else [query]
    for expanded in _domain_expanded_queries(query):
        if expanded not in queries:
            queries.append(expanded)
    queries = queries[:2] if _is_simple_relation_query(query) else queries[:4]

    rewrite_ms = round((perf_counter() - started) * 1000)
    ollama = OllamaClient()
    qdrant = QdrantClient(url=settings.qdrant_url, check_compatibility=False)
    scores: dict[int, float] = defaultdict(float)
    channels: dict[int, set[str]] = defaultdict(set)
    metadata: dict[int, dict] = defaultdict(dict)
    dense_ms = 0
    bm25_ms = 0
    wiki_ms = 0
    wiki_trace: list[dict] = []
    graph_ms = 0
    graph_trace = {"enabled": settings.graph_retrieval_enabled, "used": False, "reason": "disabled"}

    for variant in queries:
        bm25_started = perf_counter()
        _collect_bm25(
            variant,
            limit=settings.retrieval_top_k,
            allowed_documents=allowed_documents,
            scores=scores,
            channels=channels,
        )
        bm25_ms += round((perf_counter() - bm25_started) * 1000)
        wiki_started = perf_counter()
        wiki_result = _collect_wiki(
            variant,
            limit=8,
            allowed_documents=allowed_documents,
            scores=scores,
            channels=channels,
            metadata=metadata,
        )
        wiki_ms += round((perf_counter() - wiki_started) * 1000)
        if wiki_result.get("pages") or wiki_result.get("evidence_count"):
            wiki_trace.append({"query": variant, **wiki_result})

    dense_fast_path_enabled = settings.dense_fast_path_enabled and profile == "fast"
    dense_skip_reason = _dense_skip_reason(query, scores, limit) if dense_fast_path_enabled else None
    dense_skipped = dense_skip_reason is not None
    if not dense_skipped:
        dense_started = perf_counter()
        vectors = await ollama.embed(queries)
        dense_ms += round((perf_counter() - dense_started) * 1000)

        for vector in vectors:
            dense = qdrant.query_points(
                settings.qdrant_collection,
                query=vector,
                limit=settings.retrieval_top_k,
                with_payload=True,
            ).points
            for rank, hit in enumerate(dense):
                if allowed_documents is not None and int(hit.payload["document_id"]) not in allowed_documents:
                    continue
                chunk_id = int(hit.payload["chunk_id"])
                scores[chunk_id] += 1 / (60 + rank + 1)
                channels[chunk_id].add("dense")

    if settings.graph_retrieval_enabled and scores:
        graph_started = perf_counter()
        seed_ids = [
            item[0]
            for item in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:max(limit, 12)]
        ]
        graph_candidates, graph_trace = graph_candidate_scores(
            query=query,
            seed_chunk_ids=seed_ids,
            allowed_documents=allowed_documents,
            limit=settings.graph_retrieval_top_k,
        )
        for chunk_id, item in graph_candidates.items():
            scores[chunk_id] += item.score
            channels[chunk_id].add("graph")
        graph_ms = round((perf_counter() - graph_started) * 1000)

    candidate_pool_size = max(80, limit * 8)
    ordered_ids = [
        item[0]
        for item in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:candidate_pool_size]
    ]
    if not ordered_ids:
        if trace is not None:
            trace.update({
                "queries": queries,
                "retrieval_profile": profile,
                "preferred_domains": sorted(query_domains),
                "rewrite_ms": rewrite_ms,
                "retrieval_ms": round((perf_counter() - started) * 1000) - rewrite_ms,
                "dense_ms": dense_ms,
                "bm25_ms": bm25_ms,
                "wiki_ms": wiki_ms,
                "wiki": wiki_trace,
                "graph_ms": graph_ms,
                "graph": graph_trace,
                "dense_skipped": dense_skipped,
                "dense_skip_reason": dense_skip_reason or "use:dense_required",
                "rerank_ms": 0,
                "rerank_used": False,
                "rerank_reason": "skip:no_candidates",
                "candidate_count": 0,
                "document_filter": sorted(allowed_documents) if allowed_documents is not None else None,
                "results": [],
            })
        return []

    with SessionLocal() as session:
        rows = session.query(EvidenceChunk, Document).join(Document).filter(EvidenceChunk.id.in_(ordered_ids)).all()
    if allowed_documents is not None:
        rows = [(chunk, doc) for chunk, doc in rows if doc.id in allowed_documents]
    by_id = {chunk.id: (chunk, doc) for chunk, doc in rows}
    candidates = [
        {
            "chunk_id": chunk_id,
            "document_id": by_id[chunk_id][1].id,
            "title": by_id[chunk_id][1].title,
            "document_domains": sorted(document_domains(by_id[chunk_id][1].title)),
            "content": by_id[chunk_id][0].content,
            "ordinal": by_id[chunk_id][0].ordinal,
            "page": by_id[chunk_id][0].page,
            "bbox": by_id[chunk_id][0].bbox_json,
            "heading_path": by_id[chunk_id][0].heading_path,
            "rrf_score": scores[chunk_id],
            "channels": sorted(channels[chunk_id]),
            "wiki_refs": metadata.get(chunk_id, {}).get("wiki_refs", []),
        }
        for chunk_id in ordered_ids if chunk_id in by_id
    ]
    non_toc = [
        item for item in candidates
        if not _is_likely_toc_chunk(item["content"], item["heading_path"], item["page"])
    ]
    if len(non_toc) >= limit:
        candidates = non_toc
    for item in candidates:
        doc_domain_set = set(item["document_domains"])
        graph_only = set(item.get("channels", [])) == {"graph"}
        item["concept_boost"] = 0.0 if graph_only else _concept_boost(query, item)
        item["domain_boost"] = min(domain_boost(query_domains, doc_domain_set), 0.01) if graph_only else domain_boost(query_domains, doc_domain_set)
        item["final_score"] = item["rrf_score"] + item["concept_boost"] + item["domain_boost"]
    candidates.sort(key=lambda item: item["final_score"], reverse=True)

    retrieval_ms = round((perf_counter() - started) * 1000) - rewrite_ms
    rerank_started = perf_counter()
    rerank_used = False
    rerank_reason = "disabled"
    if use_rerank and settings.llm_rerank_enabled:
        rerank_used, rerank_reason = _should_rerank(query, candidates, limit)
    elif use_rerank and not settings.llm_rerank_enabled:
        rerank_reason = "skip:llm_rerank_disabled"
    if rerank_used:
        listing = "\n".join(f"ID={c['chunk_id']} {c['title']} {c['content'][:500]}" for c in candidates[:12])
        data = await ollama.generate_json(
            f"按与问题的相关性重排候选，只输出 JSON：{{\"ids\":[1,2]}}。问题：{query}\n候选：\n{listing}"
        )
        preferred = [int(value) for value in data.get("ids", []) if str(value).isdigit()]
        rank = {value: index for index, value in enumerate(preferred)}
        candidates.sort(key=lambda item: rank.get(item["chunk_id"], 999))
    if trace is not None:
        trace.update({
            "queries": queries,
            "retrieval_profile": profile,
            "preferred_domains": sorted(query_domains),
            "rewrite_ms": rewrite_ms,
            "retrieval_ms": max(0, retrieval_ms),
            "dense_ms": dense_ms,
            "bm25_ms": bm25_ms,
            "wiki_ms": wiki_ms,
            "wiki": wiki_trace,
            "graph_ms": graph_ms,
            "graph": graph_trace,
            "dense_skipped": dense_skipped,
            "dense_skip_reason": dense_skip_reason or "use:dense_required",
            "rerank_ms": round((perf_counter() - rerank_started) * 1000),
            "rerank_used": rerank_used,
            "rerank_reason": rerank_reason,
            "candidate_count": len(candidates),
            "document_filter": sorted(allowed_documents) if allowed_documents is not None else None,
            "results": [
                {
                    "chunk_id": item["chunk_id"],
                    "rrf_score": item["rrf_score"],
                    "concept_boost": item.get("concept_boost", 0.0),
                    "domain_boost": item.get("domain_boost", 0.0),
                    "document_domains": item.get("document_domains", []),
                    "channels": item["channels"],
                    "wiki_refs": item.get("wiki_refs", []),
                }
                for item in candidates[:limit]
            ],
        })
    return candidates[:limit]
