from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from app.config import get_settings
from app.database import Conversation, ImportJob, Message, SessionLocal, utcnow
from app.services.conversations import touch_conversation
from app.services.qa import answer_question


DEFAULT_QUESTION_FILE = Path("/app/knowledge/evaluations/acceptance_questions_v1.md")
DEFAULT_TITLE = "验收 - Stateflow User Guide"
QUESTION_RE = re.compile(r"^\s*\d+\.\s+(.+?)\s*$")


def load_acceptance_questions(path: Path = DEFAULT_QUESTION_FILE, sections: set[str] | None = None, limit: int = 20) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"验收问题集不存在: {path}")
    questions: list[str] = []
    current_section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current_section = line.lstrip("#").strip()
            continue
        match = QUESTION_RE.match(line)
        if not match:
            continue
        if sections and current_section not in sections:
            continue
        questions.append(match.group(1).strip())
        if len(questions) >= limit:
            break
    return questions


def get_or_create_acceptance_conversation(title: str = DEFAULT_TITLE) -> int:
    with SessionLocal() as session:
        row = session.query(Conversation).filter_by(title=title).one_or_none()
        if row:
            return row.id
        row = Conversation(title=title, pinned=True)
        session.add(row)
        session.commit()
        return row.id


def _already_asked(conversation_id: int, question: str) -> bool:
    with SessionLocal() as session:
        return bool(
            session.query(Message)
            .filter_by(conversation_id=conversation_id, role="user", content=question)
            .first()
        )


async def answer_in_conversation(conversation_id: int, question: str) -> int:
    with SessionLocal() as session:
        user_message = Message(conversation_id=conversation_id, role="user", content=question, status="completed")
        session.add(user_message)
        session.flush()
        assistant = Message(
            conversation_id=conversation_id,
            role="assistant",
            content="",
            status="generating",
            model_name=get_settings().chat_model,
        )
        session.add(assistant)
        session.commit()
        session.refresh(user_message)
        session.refresh(assistant)
        assistant_id = assistant.id

    try:
        result = await answer_question(question)
        answer = result.get("answer", "")
        citations = result.get("citations", [])
        evaluation = result.get("evaluation", {})
        evidence = result.get("evidence", [])
        trace = {"mode": "acceptance_runner", "evidence_count": len(evidence)}
        with SessionLocal() as session:
            row = session.get(Message, assistant_id)
            row.content = answer
            row.status = "completed"
            row.citations_json = json.dumps(citations, ensure_ascii=False)
            row.retrieval_trace_json = json.dumps(trace, ensure_ascii=False)
            row.evaluation_json = json.dumps(evaluation, ensure_ascii=False)
            session.commit()
    except Exception as exc:
        with SessionLocal() as session:
            row = session.get(Message, assistant_id)
            row.status = "failed"
            row.error = f"{type(exc).__name__}: {exc}"
            session.commit()
    touch_conversation(conversation_id, question)
    return assistant_id


async def wait_for_job(job_id: int, poll_seconds: int = 30) -> None:
    while True:
        with SessionLocal() as session:
            job = session.get(ImportJob, job_id)
            if not job:
                raise ValueError(f"导入任务不存在: {job_id}")
            if job.status == "completed":
                return
            if job.status == "failed":
                raise RuntimeError(f"导入任务失败: {job.error}")
        await asyncio.sleep(poll_seconds)


async def run_acceptance_after_job(
    job_id: int,
    *,
    title: str = DEFAULT_TITLE,
    question_file: Path = DEFAULT_QUESTION_FILE,
    limit: int = 20,
    poll_seconds: int = 30,
) -> dict:
    await wait_for_job(job_id, poll_seconds=poll_seconds)
    sections = {
        "D. Stateflow 基础概念",
        "E. Stateflow 与 Simulink 集成",
        "F. Stateflow 工程流程",
        "L. 引用质量检查问题",
    }
    questions = load_acceptance_questions(question_file, sections=sections, limit=limit)
    conversation_id = get_or_create_acceptance_conversation(title)
    answered = 0
    skipped = 0
    for question in questions:
        if _already_asked(conversation_id, question):
            skipped += 1
            continue
        await answer_in_conversation(conversation_id, question)
        answered += 1
    return {
        "conversation_id": conversation_id,
        "title": title,
        "questions": len(questions),
        "answered": answered,
        "skipped": skipped,
        "finished_at": utcnow().isoformat(),
    }
