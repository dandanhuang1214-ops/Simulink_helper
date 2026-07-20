from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter

from qdrant_client import QdrantClient
from sqlalchemy import text

from app.config import get_settings
from app.database import Document, EvidenceChunk, SessionLocal, WikiPage
from app.services.domains import document_domains, domain_boost, preferred_domains
from app.services.graph_retrieval import graph_candidate_scores
from app.services.evidence_selector import question_roles
from app.services.ollama import OllamaClient
from app.services.question_aspects import aspect_query_facets
from app.services.retrieval_policy import assess_retrieval_confidence, diversify_candidates
from app.services.text import lexical_tokens


WIKI_CITATION_RE = re.compile(r"\[E:(\d+)\]")


GENERIC_PRODUCT_IDENTIFIERS = {
    "autosar", "coverage", "embedded", "mathworks", "simulink", "stateflow",
}


DOMAIN_QUERY_EXPANSIONS = [
    (
        ("mcdc", "modified condition/decision", "modified condition and decision"),
        "MCDC modified condition decision coverage condition independence independently affects decision outcome full coverage",
    ),
    (("stateflow", "状态机", "状态流"), "Stateflow chart finite state machine state transition event action Simulink"),
    (("chart", "图表", "状态图"), "Stateflow chart state transition diagram"),
    (("transition", "转移", "转换"), "transition state event condition action Stateflow"),
    (("event", "事件"), "event broadcast chart execution Stateflow"),
    (("truth table", "真值表"), "truth table decision logic Stateflow"),
    (
        ("覆盖率", "覆盖数据", "覆盖报告", "coverage report", "collect coverage", "coverage results"),
        "Simulink Coverage model coverage report cvhtml cvdata cvdatagroup Generate Results for Models collect data Run Coverage Analyzer Results Explorer",
    ),
    (
        ("runnable", "可运行实体", "可运行单元"),
        "AUTOSAR runnable executable entity entry-point function code mapping events ports data access IRV configure runnable",
    ),
    (
        ("需求链接", "链接到需求", "需求追踪", "requirements traceability", "link to requirements"),
        "Simulink Test Link to Requirements Establish Requirements Traceability test case Test Sequence step current test case harness rebuild limitations",
    ),
    (("autosar", "arxml"), "AUTOSAR component composition ARXML Simulink"),
    (("xml", "arxml", "导入"), "Import AUTOSAR XML Descriptions Into Simulink ARXML Importer createComponentAsModel createCompositionAsModel updateModel shared descriptions"),
    (("simulink", "仿真"), "Simulink model simulation block signal subsystem"),
    (
        ("空白模型", "创建模型", "运行仿真", "run simulation", "create a simple model"),
        "Create a Simple Model blank model add blocks connect blocks edit parameters Run a Simulation View Simulation Results",
    ),
    (("求解器", "solver", "步长"), "solver fixed-step variable-step simulation Simulink"),
]


@dataclass(frozen=True)
class QueryVariant:
    text: str
    weight: float
    source: str


def _fts_query(value: str) -> str:
    terms = lexical_tokens(value)
    return " OR ".join(f'"{term}"' for term in terms[:12])


def _domain_expanded_queries(query: str) -> list[str]:
    lowered = query.lower()
    values = [query]
    for triggers, expansion in DOMAIN_QUERY_EXPANSIONS:
        if any(trigger.lower() in lowered for trigger in triggers):
            # The original query is already the first variant. Keeping it in
            # every expansion lets long Chinese bigrams consume the FTS token
            # budget before any English manual term is reached.
            # FTS deliberately caps each OR query at 12 tokens. Split longer
            # domain expansions into bounded facets so late terms such as
            # "model coverage report" or "View Simulation Results" are not
            # silently discarded before retrieval.
            expansion_tokens = lexical_tokens(expansion)
            values.extend(
                " ".join(expansion_tokens[index:index + 12])
                for index in range(0, len(expansion_tokens), 12)
            )
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _blend_query(original: str, expansion: str) -> str:
    """Reserve room for user terms while adding cross-language manual terms.

    FTS queries are capped at 12 tokens. Expansion-only queries can lose a
    multi-intent user's wording, while concatenating the full Chinese question
    can consume the whole budget before English manual terms are reached.
    A balanced 6+6 facet preserves both sides deterministically.
    """
    raw_original_tokens = lexical_tokens(original)
    ascii_technical = [
        token for token in raw_original_tokens
        if re.fullmatch(r"[a-z0-9_.:+/-]+", token) and len(token) >= 2
    ]
    cjk_content = [
        token for token in raw_original_tokens
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token)
        and not any(char in token for char in "的和与及在到里中呢吗啊么怎样请将把分别各自什么")
    ]
    # Technical identifiers are sparse and must survive. Fill the remaining
    # anchor budget with meaningful CJK bigrams, not boundary bigrams such as
    # “件的” or question wording such as “怎样”.
    original_tokens = list(dict.fromkeys([*ascii_technical, *cjk_content]))[:6]
    expansion_tokens = [token for token in lexical_tokens(expansion) if token not in original_tokens][:6]
    return " ".join([*original_tokens, *expansion_tokens])


def _weighted_query_plan(query: str, rewritten: list[str] | None = None) -> list[QueryVariant]:
    """Build a bounded, weighted multi-facet retrieval plan.

    The original question always has the strongest vote. Rewrites and domain
    facets may broaden recall, but cannot outvote the user's terms merely
    because several expansion rules fired.
    """
    plan = [QueryVariant(query, 1.0, "original")]
    for value in (rewritten or [])[1:3]:
        value = value.strip()
        if value and value != query:
            plan.append(QueryVariant(value, 0.80, "rewrite"))

    aspect_values = aspect_query_facets(query)
    expansion_values = _domain_expanded_queries(query)[1:]
    query_domains = preferred_domains(query)
    max_variants = 5 if len(query_domains) >= 2 else 4
    for aspect in aspect_values:
        if all(item.text != aspect for item in plan):
            plan.append(QueryVariant(aspect, 0.55, "aspect_facet"))
        if len(plan) >= max_variants:
            return plan[:max_variants]
    # Keep complete manual-heading facets: splitting them into a 6+6 blend can
    # drop the discriminative tail (for example "Generate Coverage Results for
    # Models"). Their vote remains below the original question.
    expansion_limit = max_variants - 1 if expansion_values and len(plan) < max_variants else max_variants
    for expansion in expansion_values:
        if all(item.text != expansion for item in plan):
            plan.append(QueryVariant(expansion, 0.45, "domain_facet"))
        if len(plan) >= expansion_limit:
            break
    # One balanced facet repeats the user's technical anchors alongside the
    # strongest expansion. This protects multi-intent questions without
    # multiplying the original query once per triggered rule.
    if expansion_values and len(plan) < max_variants:
        blended = _blend_query(query, expansion_values[0])
        if blended and all(item.text != blended for item in plan):
            plan.append(QueryVariant(blended, 0.30, "blended_facet"))
    return plan[:max_variants]


def _is_simple_relation_query(query: str) -> bool:
    lowered = query.lower()
    simple_relation = any(cue in lowered for cue in ("关系", "是什么", "定义", "relationship", "what is"))
    procedural_or_compare = any(cue in lowered for cue in (
        "为什么", "区别", "对比", "如何", "步骤", "流程", "原因", "怎么",
        "compare", "difference", "why", "how", "steps", "workflow",
    ))
    return simple_relation and not procedural_or_compare


def _is_procedural_query(query: str) -> bool:
    lowered = query.casefold()
    return any(cue in lowered for cue in (
        "how", "steps", "workflow", "process", "怎么", "怎样", "如何", "步骤", "流程",
        "操作顺序", "接下来", "先后",
        "创建", "导入", "配置", "映射", "运行", "收集", "生成报告",
    ))


def _should_use_wiki(query: str, query_domains: set[str]) -> tuple[bool, str]:
    roles = question_roles(query, query_domains)
    if roles & {"definition", "relationship", "comparison"}:
        return True, "concept_or_relationship"
    if roles == {"general"}:
        return True, "general_overview"
    return False, "focused_procedure_prefers_raw_evidence"


def _should_use_graph(query: str, query_domains: set[str]) -> tuple[bool, str]:
    roles = question_roles(query, query_domains)
    if roles & {"relationship", "comparison"}:
        return True, "relationship_or_comparison"
    multi_hop_cues = ("依赖", "影响", "关联", "连接", "映射", "链路", "between", "depends", "impact")
    if any(cue in query.casefold() for cue in multi_hop_cues):
        return True, "explicit_multi_hop_cue"
    return False, "simple_query_no_graph_expansion"


def _dense_skip_reason(query: str, scores: dict[int, float], limit: int) -> str | None:
    lowered = query.lower()
    technical_identifiers = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{5,}\b", query)
    has_exact_identifier = any(
        value.casefold() not in GENERIC_PRODUCT_IDENTIFIERS
        and (
            "_" in value
            or (any(char.islower() for char in value) and any(char.isupper() for char in value[1:]))
        )
        for value in technical_identifiers
    )
    if has_exact_identifier and len(scores) >= min(4, limit):
        return "skip:dense_fast_path_exact_identifier"
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


def _is_graph_led_channels(channels: set[str]) -> bool:
    """Graph/Wiki expansion is indirect until BM25 or Dense also agrees."""
    return "graph" in channels and not ({"bm25", "dense"} & channels)


EXPANSION_HEADING_STOPWORDS = {
    "autosar", "block", "blocks", "coverage", "data", "for", "model", "models",
    "simulink", "stateflow", "the", "using", "with",
}


def _expansion_heading_boost(queries: list[str], candidate: dict) -> float:
    if len(queries) <= 1:
        return 0.0
    expansion_terms = {
        token for query in queries[1:] for token in lexical_tokens(query)
        if len(token) >= 4
        and re.fullmatch(r"[a-z0-9_.:+/-]+", token)
        and token not in EXPANSION_HEADING_STOPWORDS
    }
    heading_terms = set(lexical_tokens(
        f"{candidate.get('title') or ''} {candidate.get('heading_path') or ''}"
    ))
    return min(0.060, len(expansion_terms & heading_terms) * 0.008)


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
    weight: float = 1.0,
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
        scores[chunk_id] += weight / (60 + rank + 1)
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
    weight: float = 1.0,
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
        scores[chunk_id] += weight / (70 + best_page_rank + best_local_rank)
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

    rewritten = await rewrite_query(query) if use_rewrite else [query]
    query_plan = _weighted_query_plan(query, rewritten)
    queries = [item.text for item in query_plan]

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
    wiki_enabled, wiki_reason = _should_use_wiki(query, query_domains)
    graph_query_enabled, graph_reason = _should_use_graph(query, query_domains)
    graph_trace = {
        "enabled": settings.graph_retrieval_enabled,
        "used": False,
        "reason": graph_reason if settings.graph_retrieval_enabled else "disabled",
    }

    for variant in query_plan:
        bm25_started = perf_counter()
        _collect_bm25(
            variant.text,
            limit=max(settings.retrieval_top_k, 30) if _is_procedural_query(query) else settings.retrieval_top_k,
            allowed_documents=allowed_documents,
            scores=scores,
            channels=channels,
            weight=variant.weight,
        )
        bm25_ms += round((perf_counter() - bm25_started) * 1000)
        if wiki_enabled:
            wiki_started = perf_counter()
            wiki_result = _collect_wiki(
                variant.text,
                limit=8,
                allowed_documents=allowed_documents,
                scores=scores,
                channels=channels,
                metadata=metadata,
                weight=variant.weight,
            )
            wiki_ms += round((perf_counter() - wiki_started) * 1000)
            if wiki_result.get("pages") or wiki_result.get("evidence_count"):
                wiki_trace.append({"query": variant.text, "weight": variant.weight, "source": variant.source, **wiki_result})

    dense_fast_path_enabled = settings.dense_fast_path_enabled and profile == "fast"
    dense_skip_reason = _dense_skip_reason(query, scores, limit) if dense_fast_path_enabled else None
    dense_skipped = dense_skip_reason is not None
    if not dense_skipped:
        dense_started = perf_counter()
        vectors = await ollama.embed(queries)
        dense_ms += round((perf_counter() - dense_started) * 1000)

        for variant, vector in zip(query_plan, vectors, strict=True):
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
                scores[chunk_id] += variant.weight / (60 + rank + 1)
                channels[chunk_id].add("dense")

    if settings.graph_retrieval_enabled and graph_query_enabled and scores:
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
                "query_plan": [item.__dict__ for item in query_plan],
                "retrieval_profile": profile,
                "preferred_domains": sorted(query_domains),
                "rewrite_ms": rewrite_ms,
                "retrieval_ms": round((perf_counter() - started) * 1000) - rewrite_ms,
                "dense_ms": dense_ms,
                "bm25_ms": bm25_ms,
                "wiki_ms": wiki_ms,
                "wiki": wiki_trace,
                "wiki_enabled": wiki_enabled,
                "wiki_reason": wiki_reason,
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
        channel_set = set(item.get("channels", []))
        graph_only = _is_graph_led_channels(channel_set)
        item["concept_boost"] = 0.0 if graph_only else _concept_boost(query, item)
        item["domain_boost"] = min(domain_boost(query_domains, doc_domain_set), 0.01) if graph_only else domain_boost(query_domains, doc_domain_set)
        if graph_only:
            item["channel_prior"] = -0.022
        elif channel_set == {"wiki"}:
            item["channel_prior"] = -0.006
        elif {"bm25", "dense"}.issubset(channel_set):
            item["channel_prior"] = 0.012
        else:
            item["channel_prior"] = 0.0
        item["final_score"] = (
            item["rrf_score"]
            + item["concept_boost"]
            + item["domain_boost"]
            + item["channel_prior"]
            + _expansion_heading_boost(queries, item)
        )
        item["expansion_heading_boost"] = _expansion_heading_boost(queries, item)
    candidates.sort(key=lambda item: item["final_score"], reverse=True)
    candidates, diversity_trace = diversify_candidates(candidates, limit=len(candidates))
    decision = assess_retrieval_confidence(
        query,
        candidates,
        dense_skipped=dense_skipped,
        duplicate_ratio=float(diversity_trace.get("duplicate_ratio", 0.0)),
    )

    retrieval_ms = round((perf_counter() - started) * 1000) - rewrite_ms
    rerank_started = perf_counter()
    rerank_attempted = False
    rerank_used = False
    if not use_rerank:
        rerank_reason = "skip:request_disabled"
    elif decision.tier < 3:
        rerank_reason = f"skip:tier_{decision.tier}_{decision.mode}"
    elif decision.mode != "rerank":
        rerank_reason = f"skip:tier_3_{decision.mode}"
    elif not settings.llm_rerank_enabled:
        rerank_reason = "skip:tier_3_local_rerank_disabled"
    else:
        rerank_attempted = True
        rerank_used = True
        rerank_reason = "use:tier_3_low_confidence"
    if rerank_used:
        rerank_candidates = candidates[:12]
        allowed_ids = {int(item["chunk_id"]) for item in rerank_candidates}
        listing = "\n".join(
            f"ID={c['chunk_id']}\nTITLE={c['title']}\nHEADING={c.get('heading_path') or ''}\nTEXT={c['content'][:650]}"
            for c in rerank_candidates
        )
        try:
            data = await ollama.generate_json(
                f"按与问题的相关性重排候选，只输出 JSON：{{\"ids\":[1,2]}}。问题：{query}\n候选：\n{listing}"
            )
            preferred = [
                int(value)
                for value in data.get("ids", [])
                if str(value).isdigit() and int(value) in allowed_ids
            ]
            if preferred:
                rank = {value: index for index, value in enumerate(preferred)}
                original_rank = {int(item["chunk_id"]): index for index, item in enumerate(candidates)}
                candidates.sort(
                    key=lambda item: (
                        rank.get(
                            int(item["chunk_id"]),
                            len(rank) + original_rank[int(item["chunk_id"])],
                        ),
                        original_rank[int(item["chunk_id"])],
                    )
                )
            else:
                rerank_used = False
                rerank_reason = "fallback:tier_3_empty_rerank"
        except Exception as exc:
            rerank_used = False
            rerank_reason = f"fallback:tier_3_rerank_error:{type(exc).__name__}"
    if trace is not None:
        trace.update({
            "queries": queries,
            "query_plan": [item.__dict__ for item in query_plan],
            "retrieval_profile": profile,
            "preferred_domains": sorted(query_domains),
            "rewrite_ms": rewrite_ms,
            "retrieval_ms": max(0, retrieval_ms),
            "dense_ms": dense_ms,
            "bm25_ms": bm25_ms,
            "wiki_ms": wiki_ms,
            "wiki": wiki_trace,
            "wiki_enabled": wiki_enabled,
            "wiki_reason": wiki_reason,
            "graph_ms": graph_ms,
            "graph": graph_trace,
            "dense_skipped": dense_skipped,
            "dense_skip_reason": dense_skip_reason or "use:dense_required",
            "candidate_diversity": diversity_trace,
            "retrieval_decision": {
                "tier": decision.tier,
                "mode": decision.mode,
                "confidence": decision.confidence,
                "reasons": decision.reasons,
                "signals": decision.signals,
            },
            "rerank_ms": round((perf_counter() - rerank_started) * 1000),
            "rerank_attempted": rerank_attempted,
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
                    "channel_prior": item.get("channel_prior", 0.0),
                    "expansion_heading_boost": item.get("expansion_heading_boost", 0.0),
                    "document_domains": item.get("document_domains", []),
                    "channels": item["channels"],
                    "wiki_refs": item.get("wiki_refs", []),
                }
                for item in candidates[:limit]
            ],
        })
    return candidates[:limit]
