from __future__ import annotations

import json
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import text

from app.config import get_settings
from app.database import EvidenceChunk, SessionLocal
from app.services.chunker import ChunkDraft
from app.services.ollama import OllamaClient
from app.services.storage import ensure_storage
from app.services.text import lexical_text


ProgressCallback = Callable[[int], Awaitable[None]]


async def _embed_batch_with_fallback(ollama: OllamaClient, texts: list[str]) -> list[list[float]]:
    try:
        return await ollama.embed(texts)
    except Exception:
        if len(texts) == 1:
            raise
        vectors: list[list[float]] = []
        for text_value in texts:
            vectors.extend(await ollama.embed([text_value]))
        return vectors


def _embedding_cache_dir(document_id: int) -> Path:
    path = ensure_storage() / "embeddings" / str(document_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _embedding_hash(model: str, text_value: str) -> str:
    return hashlib.sha256(f"{model}\n{text_value}".encode("utf-8")).hexdigest()


def _read_cached_vector(cache_dir: Path, ordinal: int, expected_hash: str, expected_dim: int) -> list[float] | None:
    path = cache_dir / f"{ordinal}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    vector = payload.get("vector")
    if payload.get("hash") != expected_hash:
        return None
    if not isinstance(vector, list) or len(vector) != expected_dim:
        return None
    return vector


def _write_cached_vector(cache_dir: Path, ordinal: int, digest: str, vector: list[float], model: str) -> None:
    payload = {"ordinal": ordinal, "hash": digest, "model": model, "vector": vector}
    (cache_dir / f"{ordinal}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


async def index_chunks(
    document,
    drafts: list[ChunkDraft],
    progress_callback: ProgressCallback | None = None,
) -> list[EvidenceChunk]:
    settings = get_settings()
    ollama = OllamaClient()
    embedding_texts = [
        f"标题路径：{' / '.join(draft.heading_path)}\n正文：{draft.content}" if draft.heading_path else draft.content
        for draft in drafts
    ]

    total = max(len(drafts), 1)
    vectors: list[list[float] | None] = [None] * len(drafts)
    missing_ordinals: list[int] = []
    cache_dir = _embedding_cache_dir(document.id)
    digests = [_embedding_hash(settings.embedding_model, text_value) for text_value in embedding_texts]

    if settings.embedding_cache_enabled:
        for ordinal, digest in enumerate(digests):
            cached = _read_cached_vector(cache_dir, ordinal, digest, settings.embedding_dimension)
            if cached is None:
                missing_ordinals.append(ordinal)
            else:
                vectors[ordinal] = cached
    else:
        missing_ordinals = list(range(len(drafts)))

    completed = len(drafts) - len(missing_ordinals)
    if progress_callback:
        await progress_callback(50 + round(completed / total * 25))

    batch_size = max(1, settings.embedding_batch_size)
    for offset in range(0, len(missing_ordinals), batch_size):
        ordinals = missing_ordinals[offset:offset + batch_size]
        batch = [embedding_texts[ordinal] for ordinal in ordinals]
        batch_vectors = await _embed_batch_with_fallback(ollama, batch)
        for ordinal, vector in zip(ordinals, batch_vectors, strict=True):
            vectors[ordinal] = vector
            if settings.embedding_cache_enabled:
                _write_cached_vector(cache_dir, ordinal, digests[ordinal], vector, settings.embedding_model)
        completed += len(ordinals)
        if progress_callback:
            progress = 50 + round(completed / total * 25)
            await progress_callback(progress)

    final_vectors = [vector for vector in vectors if vector is not None]
    if len(final_vectors) != len(drafts):
        raise RuntimeError("Embedding cache/index state is incomplete; not all chunks have vectors.")

    qdrant = QdrantClient(url=settings.qdrant_url, check_compatibility=False)
    if not qdrant.collection_exists(settings.qdrant_collection):
        qdrant.create_collection(
            settings.qdrant_collection,
            vectors_config=VectorParams(size=settings.embedding_dimension, distance=Distance.COSINE),
        )

    created: list[EvidenceChunk] = []
    with SessionLocal() as session:
        old_chunks = session.query(EvidenceChunk).filter_by(document_id=document.id).all()
        old_ids = [row.qdrant_id for row in old_chunks]
        if old_ids:
            qdrant.delete(settings.qdrant_collection, old_ids)
        for row in old_chunks:
            session.execute(text("DELETE FROM evidence_fts WHERE chunk_id=:id"), {"id": row.id})
        session.query(EvidenceChunk).filter_by(document_id=document.id).delete()
        session.flush()

        for ordinal, (draft, vector) in enumerate(zip(drafts, final_vectors, strict=True)):
            qdrant_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document.sha256}:{ordinal}"))
            chunk = EvidenceChunk(
                document_id=document.id,
                ordinal=ordinal,
                block_type=draft.block_type,
                heading_path=" / ".join(draft.heading_path),
                page=draft.page,
                bbox_json=json.dumps(draft.bbox) if draft.bbox else None,
                content=draft.content,
                qdrant_id=qdrant_id,
            )
            session.add(chunk)
            session.flush()
            session.execute(
                text("INSERT INTO evidence_fts(chunk_id,title,heading_path,content) VALUES(:id,:title,:heading,:content)"),
                {
                    "id": chunk.id,
                    "title": lexical_text(document.title),
                    "heading": lexical_text(chunk.heading_path),
                    "content": lexical_text(chunk.content),
                },
            )
            created.append(chunk)
        session.commit()

    qdrant.upsert(
        settings.qdrant_collection,
        points=[
            PointStruct(
                id=chunk.qdrant_id,
                vector=vector,
                payload={"chunk_id": chunk.id, "document_id": document.id, "page": chunk.page, "title": document.title},
            )
            for chunk, vector in zip(created, final_vectors, strict=True)
        ],
    )

    with SessionLocal() as session:
        rows = session.query(EvidenceChunk).filter_by(document_id=document.id).order_by(EvidenceChunk.ordinal).all()
        for index, row in enumerate(rows):
            row.previous_id = rows[index - 1].id if index else None
            row.next_id = rows[index + 1].id if index + 1 < len(rows) else None
        session.commit()
        return rows
