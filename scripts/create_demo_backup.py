from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_request(url: str, method: str = "GET") -> dict:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    knowledge_root = Path(os.getenv("KNOWLEDGE_ROOT", "/app/knowledge"))
    database_path = Path(os.getenv("DATABASE_PATH", "/app/data/app.db"))
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
    collection = os.getenv("QDRANT_COLLECTION", "simulink_documents")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = knowledge_root / "backups" / stamp
    target.mkdir(parents=True, exist_ok=False)

    sqlite_target = target / "app.db"
    source = sqlite3.connect(database_path)
    destination = sqlite3.connect(sqlite_target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    snapshot_result = json_request(
        f"{qdrant_url}/collections/{collection}/snapshots",
        method="POST",
    )
    snapshot_name = str((snapshot_result.get("result") or {}).get("name") or "")
    if not snapshot_name:
        raise RuntimeError(f"Qdrant did not return a snapshot name: {snapshot_result}")
    qdrant_target = target / snapshot_name
    urllib.request.urlretrieve(
        f"{qdrant_url}/collections/{collection}/snapshots/{snapshot_name}",
        qdrant_target,
    )
    try:
        json_request(
            f"{qdrant_url}/collections/{collection}/snapshots/{snapshot_name}",
            method="DELETE",
        )
    except Exception:
        pass

    raw_root = knowledge_root / "raw"
    raw_files = [item for item in raw_root.rglob("*") if item.is_file()] if raw_root.exists() else []
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "application_backup_format": 1,
        "sqlite": {
            "file": sqlite_target.name,
            "bytes": sqlite_target.stat().st_size,
            "sha256": sha256(sqlite_target),
        },
        "qdrant": {
            "collection": collection,
            "file": qdrant_target.name,
            "bytes": qdrant_target.stat().st_size,
            "sha256": sha256(qdrant_target),
        },
        "raw": {
            "location": str(raw_root),
            "file_count": len(raw_files),
            "bytes": sum(item.stat().st_size for item in raw_files),
            "note": "Raw files stay in the host-mounted knowledge/raw directory and are not duplicated in this backup.",
        },
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
