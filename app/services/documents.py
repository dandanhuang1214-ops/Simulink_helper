from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient
from sqlalchemy import text

from app.config import get_settings
from app.database import Document, EvidenceChunk, ImportJob, SessionLocal, WikiPage
from app.services.storage import ensure_storage


def purge_document_derivatives(document_id: int) -> dict[str, int]:
    """Remove rebuildable indexes while preserving the immutable raw source."""
    settings = get_settings()
    qdrant_ids: list[str] = []
    draft_paths: list[Path] = []
    chunk_count = 0
    wiki_count = 0

    with SessionLocal() as session:
        chunks = session.query(EvidenceChunk).filter_by(document_id=document_id).all()
        qdrant_ids = [row.qdrant_id for row in chunks]
        chunk_count = len(chunks)
        for row in chunks:
            session.execute(text("DELETE FROM evidence_fts WHERE chunk_id=:id"), {"id": row.id})

        pages = session.query(WikiPage).filter_by(source_document_id=document_id).all()
        draft_paths = [ensure_storage() / "drafts" / f"{page.slug}.md" for page in pages]
        wiki_count = len(pages)
        session.query(WikiPage).filter_by(source_document_id=document_id).delete()
        session.query(EvidenceChunk).filter_by(document_id=document_id).delete()
        session.execute(text("DELETE FROM wiki_fts"))
        session.execute(text(
            "INSERT INTO wiki_fts(page_id,title,content) SELECT id,title,content FROM wiki_pages"
        ))
        session.commit()

    if qdrant_ids:
        qdrant = QdrantClient(url=settings.qdrant_url, check_compatibility=False)
        if qdrant.collection_exists(settings.qdrant_collection):
            qdrant.delete(settings.qdrant_collection, qdrant_ids, wait=True)

    for path in draft_paths:
        path.unlink(missing_ok=True)

    return {"chunks": chunk_count, "wiki_pages": wiki_count, "vectors": len(qdrant_ids)}


def disable_document(document_id: int) -> dict[str, int]:
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if not document:
            raise LookupError("文档不存在")
        running = session.query(ImportJob).filter_by(document_id=document_id, status="running").first()
        if running:
            raise RuntimeError("文档正在重建，完成后才能停用")
        queued = session.query(ImportJob).filter_by(document_id=document_id, status="queued").all()
        for job in queued:
            job.status = "cancelled"
            job.stage = "cancelled"
            job.error = "文档已停用"
        document.enabled = False
        session.commit()
    return purge_document_derivatives(document_id)
