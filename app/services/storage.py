from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import get_settings


SUBDIRS = ("raw", "parsed", "evidence", "wiki", "drafts", "error-book", "pages", "embeddings")


def ensure_storage() -> Path:
    root = Path(get_settings().knowledge_root)
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def raw_path(digest: str, filename: str) -> Path:
    safe_name = Path(filename).name
    return ensure_storage() / "raw" / digest / safe_name


def write_immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
