from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

from app.services.parser import ParsedBlock

CHUNKING_VERSION = "structure-token-v1"
TARGET_TOKENS = 600
MAX_TOKENS = 800
OVERLAP_TOKENS = 100


@dataclass
class ChunkDraft:
    content: str
    block_type: str
    heading_path: list[str]
    page: int | None
    bbox: list[float] | None
    estimated_tokens: int
    source_method: str = "unknown"


def estimate_tokens(value: str) -> int:
    """Conservative tokenizer-free estimate for mixed Chinese/English technical text."""
    cjk = len(re.findall(r"[\u3400-\u9fff]", value))
    non_cjk = len(re.sub(r"[\s\u3400-\u9fff]", "", value))
    return cjk + math.ceil(non_cjk / 4)


def _sentences(value: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n{2,}|(?<=[.!?])\s+(?=[A-Z0-9])", value.strip())
    return [part.strip() for part in parts if part.strip()]


def _split_long_unit(value: str, max_tokens: int) -> list[str]:
    if estimate_tokens(value) <= max_tokens:
        return [value]
    pieces: list[str] = []
    buffer = ""
    for char in value:
        candidate = buffer + char
        if buffer and estimate_tokens(candidate) > max_tokens:
            pieces.append(buffer)
            buffer = char
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    return pieces


def _union_bbox(boxes: list[list[float] | None]) -> list[float] | None:
    values = [box for box in boxes if box and len(box) == 4]
    if not values:
        return None
    return [min(box[0] for box in values), min(box[1] for box in values), max(box[2] for box in values), max(box[3] for box in values)]


def _table_chunks(block: ParsedBlock, max_tokens: int) -> list[ChunkDraft]:
    lines = [line for line in block.content.splitlines() if line.strip()]
    if estimate_tokens(block.content) <= max_tokens or len(lines) < 3:
        return [ChunkDraft(block.content, "table", block.heading_path or [], block.page, block.bbox, estimate_tokens(block.content), block.source_method)]
    header = lines[:2]
    rows = lines[2:]
    chunks: list[ChunkDraft] = []
    current = header.copy()
    for row in rows:
        candidate = "\n".join([*current, row])
        if len(current) > 2 and estimate_tokens(candidate) > max_tokens:
            content = "\n".join(current)
            chunks.append(ChunkDraft(content, "table", block.heading_path or [], block.page, block.bbox, estimate_tokens(content), block.source_method))
            current = [*header, current[-1], row]
        else:
            current.append(row)
    if len(current) > 2:
        content = "\n".join(current)
        chunks.append(ChunkDraft(content, "table", block.heading_path or [], block.page, block.bbox, estimate_tokens(content), block.source_method))
    return chunks


def _prose_group(
    blocks: list[ParsedBlock], target_tokens: int, max_tokens: int, overlap_tokens: int
) -> list[ChunkDraft]:
    if not blocks:
        return []
    units: list[tuple[str, list[float] | None]] = []
    for block in blocks:
        for sentence in _sentences(block.content):
            units.extend((piece, block.bbox) for piece in _split_long_unit(sentence, max_tokens))

    chunks: list[ChunkDraft] = []
    current: list[tuple[str, list[float] | None]] = []
    current_tokens = 0
    has_new_content = False

    def emit() -> None:
        if not current:
            return
        content = " ".join(item[0] for item in current).strip()
        chunks.append(ChunkDraft(
            content=content,
            block_type="text",
            heading_path=blocks[0].heading_path or [],
            page=blocks[0].page,
            bbox=_union_bbox([item[1] for item in current]),
            estimated_tokens=estimate_tokens(content),
            source_method=blocks[0].source_method,
        ))

    for unit, bbox in units:
        unit_tokens = estimate_tokens(unit)
        if current and current_tokens + unit_tokens > max_tokens:
            emit()
            overlap: list[tuple[str, list[float] | None]] = []
            overlap_size = 0
            for previous in reversed(current):
                size = estimate_tokens(previous[0])
                if overlap and overlap_size + size > overlap_tokens:
                    break
                overlap.insert(0, previous)
                overlap_size += size
            current = overlap
            current_tokens = overlap_size
            has_new_content = False
        current.append((unit, bbox))
        current_tokens += unit_tokens
        has_new_content = True
        if current_tokens >= target_tokens:
            emit()
            overlap = []
            overlap_size = 0
            for previous in reversed(current):
                size = estimate_tokens(previous[0])
                if overlap and overlap_size + size > overlap_tokens:
                    break
                overlap.insert(0, previous)
                overlap_size += size
            current = overlap
            current_tokens = overlap_size
            has_new_content = False
    if current and has_new_content:
        emit()
    return chunks


def chunk_blocks(
    blocks: list[ParsedBlock],
    target_tokens: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    prose: list[ParsedBlock] = []

    def flush_prose() -> None:
        if prose:
            chunks.extend(_prose_group(prose, target_tokens, max_tokens, overlap_tokens))
            prose.clear()

    for block in blocks:
        if not block.content.strip():
            continue
        if block.block_type == "table":
            flush_prose()
            chunks.extend(_table_chunks(block, max_tokens))
            continue
        if block.block_type in {"formula", "image"}:
            flush_prose()
            content = block.content.strip()
            chunks.append(ChunkDraft(content, block.block_type, block.heading_path or [], block.page, block.bbox, estimate_tokens(content), block.source_method))
            continue
        if prose and (prose[0].page != block.page or prose[0].heading_path != block.heading_path):
            flush_prose()
        prose.append(block)
    flush_prose()
    return chunks


def chunk_manifest(drafts: list[ChunkDraft]) -> dict:
    return {
        "version": CHUNKING_VERSION,
        "target_tokens": TARGET_TOKENS,
        "max_tokens": MAX_TOKENS,
        "overlap_tokens": OVERLAP_TOKENS,
        "estimator": "cjk_chars_plus_non_cjk_chars_div_4",
        "chunks": [asdict(draft) for draft in drafts],
    }
