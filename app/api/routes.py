from __future__ import annotations

import asyncio
import json
import re
from time import perf_counter
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.config import get_settings
from app.database import Conversation, Document, Evaluation, EvidenceChunk, ImportJob, MemoryItem, Message, SessionLocal, WikiPage
from app.schemas import (
    ChatRequest, ConversationCreate, ConversationMessageRequest, ConversationUpdate, DocumentRead,
    JobRead, MemoryUpdate, MessageFeedbackRequest, ReviewRequest, SearchRequest,
)
from app.services.conversations import (
    active_memories, conversation_dict, extract_safe_memories, message_dict, recent_context, touch_conversation,
)
from app.services.coverage import insufficient_coverage_answer
from app.services.evidence_snippets import answer_generation_budget
from app.services.graph import compile_knowledge_graph
from app.services.ollama import OllamaClient
from app.services.qa import (
    answer_question,
    build_answer_prompt,
    conversational_reply,
    ensure_evidence_citations,
    judge_answer,
)
from app.services.retrieval import hybrid_search
from app.services.retrieval_pipeline import retrieve_evidence_with_coverage
from app.services.storage import ensure_storage, raw_path, sha256_bytes, write_immutable
from app.services.wiki import publish_page, wiki_graph

router = APIRouter(prefix="/api")
ALLOWED_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _document_payload(document: Document) -> dict:
    payload = DocumentRead.model_validate(document).model_dump()
    metadata_path = ensure_storage() / "parsed" / str(document.id) / "metadata.json"
    if metadata_path.exists():
        try:
            payload["parse_quality"] = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload["parse_quality"] = {"status": "WARNING", "notes": ["解析质量 metadata 文件格式异常。"]}
    return payload


def _insufficient_evidence_answer(source_filter: dict, trace: dict) -> str:
    tips = [
        "当前资料范围内没有找到足够证据，因此我不直接编造答案。",
    ]
    if source_filter.get("document_ids") or source_filter.get("releases"):
        tips.append("你现在启用了资料筛选，可以先切回“全库”再试。")
    queries = trace.get("queries") or []
    if queries:
        tips.append(f"本轮实际检索词：{' / '.join(queries)}。可以换成更接近文档标题、模块名或英文术语的问法。")
    tips.append("如果这是新领域问题，建议先导入对应的 Simulink、AUTOSAR 或 Stateflow 文档。")
    return "\n".join(f"- {item}" for item in tips)


def _looks_mojibake(value: str) -> bool:
    if not value:
        return False
    markers = ("æ", "ç", "è", "ä", "å", "ï¼", "ã", "", "", "", "")
    marker_count = sum(value.count(marker) for marker in markers)
    return marker_count >= 2


def _is_clean_training_pair(instruction: str, output: str) -> bool:
    if not instruction.strip() or not output.strip():
        return False
    if _looks_mojibake(instruction) or _looks_mojibake(output):
        return False
    if instruction.strip().lower() in {"test", "测试", "链路测试", "规则路由复测"}:
        return False
    return True


async def _run_background_judge(
    message_id: int,
    question: str,
    answer: str,
    context: str,
    trace: dict,
) -> None:
    judge_started = perf_counter()
    try:
        evaluation = await judge_answer(question, answer, context)
        scores = [
            evaluation.get(key, 0)
            for key in ("retrieval_sufficiency", "faithfulness", "citation_coverage", "completeness")
        ]
        evaluation["passed"] = bool(scores) and min(scores) >= get_settings().judge_threshold
        trace["judge_ms"] = round((perf_counter() - judge_started) * 1000)
        with SessionLocal() as session:
            row = session.get(Message, message_id)
            if not row:
                return
            row.evaluation_json = json.dumps(evaluation, ensure_ascii=False)
            row.retrieval_trace_json = json.dumps(trace, ensure_ascii=False)
            session.add(Evaluation(
                question=question, answer=answer,
                retrieval_sufficiency=float(evaluation.get("retrieval_sufficiency", 0)),
                faithfulness=float(evaluation.get("faithfulness", 0)),
                citation_coverage=float(evaluation.get("citation_coverage", 0)),
                completeness=float(evaluation.get("completeness", 0)),
                passed=evaluation["passed"], details_json=json.dumps(evaluation, ensure_ascii=False),
            ))
            session.commit()
    except Exception as exc:
        with SessionLocal() as session:
            row = session.get(Message, message_id)
            if row:
                row.evaluation_json = json.dumps({
                    "passed": False,
                    "background_error": f"{type(exc).__name__}: {exc}",
                }, ensure_ascii=False)
                session.commit()


@router.post("/documents", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    parse_mode: str = Form(default="auto"),
    release: str | None = Form(default=None),
    language: str = Form(default="zh"),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, "仅支持 MD、TXT、DOCX 和 PDF")
    if parse_mode not in {"auto", "text", "ocr", "vlm"}:
        raise HTTPException(422, "parse_mode 必须是 auto/text/ocr/vlm")
    data = await file.read()
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "文件过大")
    digest = sha256_bytes(data)
    with SessionLocal() as session:
        existing = session.query(Document).filter_by(sha256=digest).one_or_none()
        if existing:
            latest = session.query(ImportJob).filter_by(document_id=existing.id).order_by(ImportJob.id.desc()).first()
            return {"document": _document_payload(existing), "job": JobRead.model_validate(latest), "duplicate": True}
        path = raw_path(digest, file.filename or f"document{suffix}")
        write_immutable(path, data)
        document = Document(
            title=title or Path(file.filename or "document").stem,
            filename=file.filename or path.name,
            sha256=digest,
            media_type=file.content_type or "application/octet-stream",
            storage_path=str(path),
            parse_mode=parse_mode,
            release=release,
            language=language,
        )
        session.add(document)
        session.flush()
        job = ImportJob(document_id=document.id)
        session.add(job)
        session.commit()
        session.refresh(document)
        session.refresh(job)
        return {"document": _document_payload(document), "job": JobRead.model_validate(job), "duplicate": False}


@router.get("/documents", response_model=list[DocumentRead])
def list_documents() -> list[dict]:
    with SessionLocal() as session:
        documents = session.query(Document).order_by(Document.id.desc()).all()
        return [_document_payload(document) for document in documents]


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: int) -> ImportJob:
    with SessionLocal() as session:
        job = session.get(ImportJob, job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: int) -> ImportJob:
    with SessionLocal() as session:
        job = session.get(ImportJob, job_id)
        if not job or job.status != "failed":
            raise HTTPException(409, "只有失败任务可以重试")
        job.status, job.stage, job.progress, job.error = "queued", "queued", 0, None
        job.document.status, job.document.error = "queued", None
        session.commit()
        return job


@router.post("/documents/{document_id}/reindex", response_model=JobRead, status_code=202)
def reindex_document(document_id: int) -> ImportJob:
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if not document:
            raise HTTPException(404, "文档不存在")
        active = session.query(ImportJob).filter(
            ImportJob.document_id == document_id,
            ImportJob.status.in_(["queued", "running"]),
        ).first()
        if active:
            return active
        document.status = "queued"
        job = ImportJob(document_id=document_id)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


@router.post("/search")
async def search(request: SearchRequest) -> dict:
    results = await hybrid_search(request.query, request.limit, request.use_rewrite, request.use_rerank)
    return {"query": request.query, "results": results}


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    async def events():
        yield "event: status\ndata: " + json.dumps({"stage": "retrieving"}, ensure_ascii=False) + "\n\n"
        task = asyncio.create_task(answer_question(request.question))
        elapsed = 0
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=10)
                if done:
                    break
                elapsed += 10
                yield "event: status\ndata: " + json.dumps(
                    {"stage": "processing", "elapsed_seconds": elapsed}, ensure_ascii=False
                ) + "\n\n"
            result = await task
            yield "event: answer\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            yield "event: error\ndata: " + json.dumps(
                {"message": f"问答链路执行失败: {type(exc).__name__}: {exc}"}, ensure_ascii=False
            ) + "\n\n"
    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations", status_code=201)
def create_conversation(request: ConversationCreate) -> dict:
    with SessionLocal() as session:
        row = Conversation(title=request.title)
        session.add(row)
        session.commit()
        session.refresh(row)
        return conversation_dict(row)


@router.get("/conversations")
def list_conversations() -> list[dict]:
    with SessionLocal() as session:
        rows = session.query(Conversation).order_by(Conversation.pinned.desc(), Conversation.updated_at.desc()).all()
        return [conversation_dict(row) for row in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int) -> dict:
    with SessionLocal() as session:
        row = session.get(Conversation, conversation_id)
        if not row:
            raise HTTPException(404, "会话不存在")
        return conversation_dict(row, include_messages=True)


@router.patch("/conversations/{conversation_id}")
def update_conversation(conversation_id: int, request: ConversationUpdate) -> dict:
    with SessionLocal() as session:
        row = session.get(Conversation, conversation_id)
        if not row:
            raise HTTPException(404, "会话不存在")
        if request.title is not None:
            row.title = request.title
        if request.pinned is not None:
            row.pinned = request.pinned
        if request.source_filter is not None:
            row.source_filter_json = json.dumps(request.source_filter, ensure_ascii=False)
        session.commit()
        session.refresh(row)
        return conversation_dict(row)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int) -> dict:
    with SessionLocal() as session:
        row = session.get(Conversation, conversation_id)
        if not row:
            raise HTTPException(404, "会话不存在")
        session.delete(row)
        session.commit()
        return {"deleted": True}


@router.post("/conversations/{conversation_id}/messages/stream")
async def conversation_message_stream(conversation_id: int, request: ConversationMessageRequest) -> StreamingResponse:
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "会话不存在")
        source_filter = json.loads(conversation.source_filter_json or "{}")
        user_message = Message(conversation_id=conversation_id, role="user", content=request.content, status="completed")
        session.add(user_message)
        session.flush()
        assistant = Message(
            conversation_id=conversation_id, role="assistant", content="", status="generating",
            model_name=get_settings().chat_model,
        )
        session.add(assistant)
        session.commit()
        session.refresh(user_message)
        session.refresh(assistant)
        user_message_id, assistant_id = user_message.id, assistant.id
    touch_conversation(conversation_id, request.content)

    async def events():
        started = perf_counter()
        yield _sse("message.created", {"user_message_id": user_message_id, "assistant_message_id": assistant_id})
        direct = conversational_reply(request.content)
        if direct:
            answer = direct["answer"]
            yield _sse("answer.delta", {"message_id": assistant_id, "delta": answer})
            with SessionLocal() as session:
                row = session.get(Message, assistant_id)
                row.content, row.status = answer, "completed"
                row.latency_ms = round((perf_counter() - started) * 1000)
                session.commit()
            yield _sse("answer.completed", {"message_id": assistant_id, **direct})
            yield _sse("done", {})
            return

        trace: dict = {}
        history = recent_context(conversation_id, user_message_id, turns=6)
        use_rewrite = bool(history) and bool(re.search(r"(它|这个|上述|前面|该|其|那个)", request.content))
        try:
            yield _sse("stage.started", {"stage": "retrieval", "label": "正在检索知识库"})
            retrieval_started = perf_counter()
            retrieval = await retrieve_evidence_with_coverage(
                request.content, use_rewrite=use_rewrite, use_rerank=True,
                document_ids=source_filter.get("document_ids"), releases=source_filter.get("releases"), trace=trace,
            )
            candidates = retrieval.candidates
            evidence = retrieval.evidence
            yield _sse("stage.completed", {
                "stage": "retrieval", "elapsed_ms": round((perf_counter() - retrieval_started) * 1000),
                "candidate_count": len(candidates), "selected_count": len(evidence), "trace": trace,
            })
            if not evidence:
                answer = _insufficient_evidence_answer(source_filter, trace)
                with SessionLocal() as session:
                    row = session.get(Message, assistant_id)
                    row.content, row.status = answer, "completed"
                    row.retrieval_trace_json = json.dumps(trace, ensure_ascii=False)
                    row.evaluation_json = json.dumps({"passed": False, "reason": "retrieval_empty"}, ensure_ascii=False)
                    row.latency_ms = round((perf_counter() - started) * 1000)
                    session.commit()
                yield _sse("answer.delta", {"message_id": assistant_id, "delta": answer})
                yield _sse("answer.completed", {
                    "message_id": assistant_id, "answer": answer, "citations": [], "evidence": [], "trace": trace,
                    "evaluation": {"passed": False, "reason": "retrieval_empty"},
                })
                yield _sse("done", {})
                return

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
                with SessionLocal() as session:
                    row = session.get(Message, assistant_id)
                    row.content, row.status = answer, "completed"
                    row.retrieval_trace_json = json.dumps(trace, ensure_ascii=False)
                    row.evaluation_json = json.dumps(evaluation, ensure_ascii=False)
                    row.latency_ms = round((perf_counter() - started) * 1000)
                    session.commit()
                yield _sse("answer.delta", {"message_id": assistant_id, "delta": answer})
                yield _sse("answer.completed", {
                    "message_id": assistant_id, "answer": answer, "citations": [], "evidence": evidence, "trace": trace,
                    "evaluation": evaluation,
                })
                yield _sse("done", {})
                return

            prompt, context = build_answer_prompt(request.content, evidence, history, active_memories(), trace=trace)
            yield _sse("stage.started", {"stage": "generation", "label": "正在生成证据式回答"})
            answer_parts: list[str] = []
            generation_started = perf_counter()
            generation_tokens = answer_generation_budget(request.content)
            trace["generation_budget"] = generation_tokens
            async for token in OllamaClient().generate_stream(prompt, num_predict=generation_tokens):
                answer_parts.append(token)
                yield _sse("answer.delta", {"message_id": assistant_id, "delta": token})
            answer = "".join(answer_parts).strip()
            trace["generation_ms"] = round((perf_counter() - generation_started) * 1000)
            answer, citations = ensure_evidence_citations(answer, evidence)
            with SessionLocal() as session:
                row = session.get(Message, assistant_id)
                row.content, row.status = answer, "completed"
                row.citations_json = json.dumps(citations, ensure_ascii=False)
                row.retrieval_trace_json = json.dumps(trace, ensure_ascii=False)
                row.latency_ms = round((perf_counter() - started) * 1000)
                session.commit()
            yield _sse("stage.completed", {"stage": "generation", "elapsed_ms": trace["generation_ms"]})
            yield _sse("answer.completed", {
                "message_id": assistant_id, "answer": answer, "citations": citations, "evidence": evidence, "trace": trace,
            })

            memories = extract_safe_memories(user_message_id, request.content)
            for memory in memories:
                yield _sse("memory.created", memory)

            yield _sse("stage.started", {"stage": "judge", "label": "正在后台评估"})
            asyncio.create_task(_run_background_judge(assistant_id, request.content, answer, context, trace.copy()))
            yield _sse("done", {})
        except asyncio.CancelledError:
            with SessionLocal() as session:
                row = session.get(Message, assistant_id)
                if row:
                    row.status = "cancelled"
                    session.commit()
            raise
        except Exception as exc:
            with SessionLocal() as session:
                row = session.get(Message, assistant_id)
                if row:
                    row.status, row.error = "failed", f"{type(exc).__name__}: {exc}"
                    session.commit()
            yield _sse("error", {"message_id": assistant_id, "message": f"问答链路失败: {type(exc).__name__}: {exc}"})
            yield _sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/messages/{message_id}")
def get_message(message_id: int) -> dict:
    with SessionLocal() as session:
        row = session.get(Message, message_id)
        if not row:
            raise HTTPException(404, "消息不存在")
        return message_dict(row)


@router.post("/messages/{message_id}/regenerate")
def regenerate_message(message_id: int) -> dict:
    with SessionLocal() as session:
        row = session.get(Message, message_id)
        if not row or row.role != "assistant":
            raise HTTPException(404, "助手消息不存在")
        user = session.query(Message).filter(
            Message.conversation_id == row.conversation_id, Message.role == "user", Message.id < row.id
        ).order_by(Message.id.desc()).first()
        if not user:
            raise HTTPException(409, "未找到对应的用户问题")
        return {"conversation_id": row.conversation_id, "content": user.content}


@router.post("/messages/{message_id}/feedback")
def message_feedback(message_id: int, request: MessageFeedbackRequest) -> dict:
    with SessionLocal() as session:
        row = session.get(Message, message_id)
        if not row:
            raise HTTPException(404, "消息不存在")
        row.feedback = request.feedback
        session.commit()
        return {"id": row.id, "feedback": row.feedback}


@router.get("/memories")
def list_memories() -> list[dict]:
    with SessionLocal() as session:
        rows = session.query(MemoryItem).order_by(MemoryItem.updated_at.desc()).all()
        return [{
            "id": row.id, "memory_type": row.memory_type, "content": row.content,
            "source_message_id": row.source_message_id, "confidence": row.confidence,
            "active": row.active, "created_at": row.created_at, "updated_at": row.updated_at,
        } for row in rows]


@router.patch("/memories/{memory_id}")
def update_memory(memory_id: int, request: MemoryUpdate) -> dict:
    with SessionLocal() as session:
        row = session.get(MemoryItem, memory_id)
        if not row:
            raise HTTPException(404, "记忆不存在")
        if request.content is not None:
            row.content = request.content
        if request.active is not None:
            row.active = request.active
        session.commit()
        return {"id": row.id, "content": row.content, "active": row.active}


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int) -> dict:
    with SessionLocal() as session:
        row = session.get(MemoryItem, memory_id)
        if not row:
            raise HTTPException(404, "记忆不存在")
        session.delete(row)
        session.commit()
        return {"deleted": True}


@router.get("/wiki/pages")
def list_wiki_pages() -> list[dict]:
    with SessionLocal() as session:
        pages = session.query(WikiPage).order_by(WikiPage.updated_at.desc()).all()
        return [{"slug": p.slug, "title": p.title, "status": p.status, "type": p.page_type, "updated_at": p.updated_at} for p in pages]


@router.get("/wiki/pages/{slug}")
def get_wiki_page(slug: str) -> dict:
    with SessionLocal() as session:
        page = session.query(WikiPage).filter_by(slug=slug).one_or_none()
        if not page:
            raise HTTPException(404, "Wiki页面不存在")
        return {"slug": page.slug, "title": page.title, "content": page.content, "status": page.status, "links": json.loads(page.links_json)}


@router.patch("/wiki/pages/{slug}")
def review_wiki_page(slug: str, request: ReviewRequest) -> dict:
    with SessionLocal() as session:
        page = session.query(WikiPage).filter_by(slug=slug).one_or_none()
        if not page:
            raise HTTPException(404, "Wiki页面不存在")
        page.status = request.status
        session.commit()
        if request.status == "published":
            publish_page(page)
        return {"slug": page.slug, "status": page.status}


@router.get("/wiki/graph")
def get_wiki_graph() -> dict:
    return wiki_graph()


@router.post("/wiki/graph/compile")
async def compile_wiki_graph(use_llm: bool = True, limit_pages: int = 80) -> dict:
    return await compile_knowledge_graph(use_llm=use_llm, limit_pages=limit_pages)


@router.get("/evidence/{chunk_id}")
def get_evidence_chunk(chunk_id: int) -> dict:
    with SessionLocal() as session:
        row = session.query(EvidenceChunk, Document).join(Document).filter(EvidenceChunk.id == chunk_id).one_or_none()
        if not row:
            raise HTTPException(404, "证据块不存在")
        chunk, document = row
        return {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "title": document.title,
            "page": chunk.page,
            "bbox": chunk.bbox_json,
            "heading_path": chunk.heading_path,
            "block_type": chunk.block_type,
            "content": chunk.content,
        }


@router.get("/sources/{document_id}/pages/{page}")
def source_page(document_id: int, page: int) -> FileResponse:
    path = Path(get_settings().knowledge_root) / "pages" / str(document_id) / f"{page}.png"
    if not path.exists():
        raise HTTPException(404, "页面图片不存在")
    return FileResponse(path, media_type="image/png")


@router.get("/evaluations")
def evaluations() -> list[dict]:
    with SessionLocal() as session:
        rows = session.query(Evaluation).order_by(Evaluation.id.desc()).limit(100).all()
        return [{"id": r.id, "question": r.question, "answer": r.answer, "passed": r.passed,
                 "scores": {"retrieval": r.retrieval_sufficiency, "faithfulness": r.faithfulness,
                            "citations": r.citation_coverage, "completeness": r.completeness},
                 "created_at": r.created_at} for r in rows]


@router.get("/training-samples")
def export_training_samples(limit: int = 200, clean: bool = False) -> list[dict]:
    """Export lightweight RAG training/evaluation samples from local conversation logs."""
    limit = max(1, min(limit, 1000))
    samples: list[dict] = []
    with SessionLocal() as session:
        assistants = (
            session.query(Message)
            .filter(Message.role == "assistant", Message.status == "completed", Message.content != "")
            .order_by(Message.id.desc())
            .limit(limit)
            .all()
        )
        for assistant in assistants:
            user = (
                session.query(Message)
                .filter(
                    Message.conversation_id == assistant.conversation_id,
                    Message.role == "user",
                    Message.id < assistant.id,
                )
                .order_by(Message.id.desc())
                .first()
            )
            if not user:
                continue
            if clean and not _is_clean_training_pair(user.content, assistant.content):
                continue
            samples.append({
                "conversation_id": assistant.conversation_id,
                "user_message_id": user.id,
                "assistant_message_id": assistant.id,
                "instruction": user.content,
                "output": assistant.content,
                "citations": json.loads(assistant.citations_json or "[]"),
                "retrieval_trace": json.loads(assistant.retrieval_trace_json or "{}"),
                "evaluation": json.loads(assistant.evaluation_json or "{}"),
                "feedback": assistant.feedback,
                "model_name": assistant.model_name,
                "latency_ms": assistant.latency_ms,
                "created_at": assistant.created_at,
            })
    return samples
