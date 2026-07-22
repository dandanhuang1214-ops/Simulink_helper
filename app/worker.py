from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

from app.config import get_settings
from app.database import Document, ImportJob, SessionLocal, initialize_database, utcnow
from app.services.chunker import chunk_blocks, chunk_manifest
from app.services.indexer import index_chunks
from app.services.parser import parse_document
from app.services.storage import ensure_storage
from app.services.wiki import compile_source_page


def claim_job() -> int | None:
    with SessionLocal() as session:
        job = (
            session.query(ImportJob)
            .join(Document)
            .filter(ImportJob.status == "queued", Document.enabled.is_(True))
            .order_by(ImportJob.id)
            .first()
        )
        if not job:
            return None
        job.status = "running"
        job.stage = "parsing"
        job.progress = 5
        job.attempts += 1
        job.started_at = utcnow()
        job.document.status = "processing"
        session.commit()
        return job.id


def update_job(job_id: int, stage: str, progress: int) -> None:
    with SessionLocal() as session:
        job = session.get(ImportJob, job_id)
        if job:
            job.stage = stage
            job.progress = progress
            session.commit()


def recover_interrupted_jobs() -> None:
    with SessionLocal() as session:
        jobs = session.query(ImportJob).filter_by(status="running").all()
        for job in jobs:
            job.status = "queued"
            job.stage = "queued"
            job.progress = 0
            if job.document:
                job.document.status = "queued"
        session.commit()


async def process_job(job_id: int) -> None:
    with SessionLocal() as session:
        job = session.get(ImportJob, job_id)
        document_id = job.document_id
        document = session.get(Document, document_id)
        path = Path(document.storage_path)
        parse_mode = document.parse_mode

    try:
        blocks = await asyncio.to_thread(parse_document, path, document_id, parse_mode)
        update_job(job_id, "chunking", 35)
        drafts = chunk_blocks(blocks)
        if not drafts:
            raise ValueError("解析完成，但没有生成证据块。请检查文档是否为空、是否为扫描 PDF，或解析质量是否为 POOR。")
        evidence_dir = ensure_storage() / "evidence" / str(document_id)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "manifest.json").write_text(
            json.dumps(chunk_manifest(drafts), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        update_job(job_id, "indexing", 50)
        with SessionLocal() as session:
            document = session.get(Document, document_id)
            chunks = await index_chunks(document, drafts, progress_callback=lambda progress: asyncio.to_thread(update_job, job_id, "indexing", progress))
        update_job(job_id, "wiki", 80)
        with SessionLocal() as session:
            document = session.get(Document, document_id)
            await compile_source_page(document, chunks)
        with SessionLocal() as session:
            job = session.get(ImportJob, job_id)
            job.status = "completed"
            job.stage = "completed"
            job.progress = 100
            job.finished_at = utcnow()
            job.document.status = "ready"
            job.document.error = None
            session.commit()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        ensure_storage().joinpath("error-book", f"job-{job_id}.log").write_text(traceback.format_exc(), encoding="utf-8")
        with SessionLocal() as session:
            job = session.get(ImportJob, job_id)
            job.status = "failed"
            job.stage = "failed"
            job.error = error
            job.finished_at = utcnow()
            job.document.status = "failed"
            job.document.error = error
            session.commit()


async def main() -> None:
    initialize_database()
    ensure_storage()
    recover_interrupted_jobs()
    settings = get_settings()
    while True:
        job_id = claim_job()
        if job_id is None:
            await asyncio.sleep(settings.worker_poll_seconds)
        else:
            await process_job(job_id)


if __name__ == "__main__":
    asyncio.run(main())
