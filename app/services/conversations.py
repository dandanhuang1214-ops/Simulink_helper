from __future__ import annotations

import json
import re

from app.database import Conversation, MemoryItem, Message, SessionLocal, utcnow


def conversation_dict(row: Conversation, include_messages: bool = False) -> dict:
    result = {
        "id": row.id,
        "title": row.title,
        "pinned": row.pinned,
        "source_filter": json.loads(row.source_filter_json or "{}"),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if include_messages:
        result["messages"] = [message_dict(item) for item in row.messages]
    return result


def message_dict(row: Message) -> dict:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "role": row.role,
        "content": row.content,
        "status": row.status,
        "citations": json.loads(row.citations_json or "[]"),
        "retrieval_trace": json.loads(row.retrieval_trace_json or "{}"),
        "evaluation": json.loads(row.evaluation_json or "{}"),
        "model_name": row.model_name,
        "latency_ms": row.latency_ms,
        "error": row.error,
        "feedback": row.feedback,
        "created_at": row.created_at,
    }


def recent_context(conversation_id: int, before_message_id: int, turns: int = 6) -> list[dict]:
    with SessionLocal() as session:
        rows = (
            session.query(Message)
            .filter(Message.conversation_id == conversation_id, Message.id < before_message_id, Message.status == "completed")
            .order_by(Message.id.desc())
            .limit(turns * 2)
            .all()
        )
    return [{"role": row.role, "content": row.content} for row in reversed(rows)]


def active_memories() -> list[str]:
    with SessionLocal() as session:
        rows = session.query(MemoryItem).filter_by(active=True).order_by(MemoryItem.updated_at.desc()).limit(20).all()
    return [row.content for row in rows]


def extract_safe_memories(message_id: int, content: str) -> list[dict]:
    """Extract a deliberately small allow-list of low-risk preferences without another LLM call."""
    candidates: list[tuple[str, str, float]] = []
    lowered = content.lower()
    release = re.search(r"\bR20\d{2}[ab]\b", content, re.IGNORECASE)
    if release:
        candidates.append(("simulink_release", f"用户主要使用 Simulink {release.group(0).upper()}", 0.98))
    if "中文回答" in content or "用中文" in content:
        candidates.append(("response_style", "用户偏好中文回答", 0.98))
    if "简洁" in content and ("回答" in content or "风格" in content):
        candidates.append(("response_style", "用户偏好简洁回答", 0.85))
    if "详细" in content and ("回答" in content or "步骤" in content):
        candidates.append(("response_style", "用户偏好详细步骤", 0.85))
    project = re.search(r"(?:我的项目是|项目名叫|项目叫)[「『\"']?([^\n，。,]{2,40})", content)
    if project:
        candidates.append(("project", f"当前项目：{project.group(1).strip()}", 0.82))

    created: list[dict] = []
    with SessionLocal() as session:
        for kind, value, confidence in candidates:
            exists = session.query(MemoryItem).filter_by(memory_type=kind, content=value, active=True).first()
            if exists:
                continue
            row = MemoryItem(memory_type=kind, content=value, source_message_id=message_id, confidence=confidence)
            session.add(row)
            session.flush()
            created.append({"id": row.id, "memory_type": kind, "content": value, "confidence": confidence})
        session.commit()
    return created


def touch_conversation(conversation_id: int, first_question: str | None = None) -> None:
    with SessionLocal() as session:
        row = session.get(Conversation, conversation_id)
        if not row:
            return
        if first_question and row.title == "新对话":
            row.title = first_question.strip()[:36]
        row.updated_at = utcnow()
        session.commit()
