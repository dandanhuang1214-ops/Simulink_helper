from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import fitz


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: Path) -> dict:
    document = fitz.open(path)
    page_count = len(document)
    if page_count <= 120:
        sample_indexes = list(range(page_count))
    else:
        step = max(1, page_count // 40)
        sample_indexes = sorted(set([0, page_count - 1, *range(0, page_count, step)]))

    text_chars = 0
    empty_pages = 0
    image_count = 0
    extraction_errors: list[str] = []
    for index in sample_indexes:
        try:
            page = document[index]
            text = page.get_text("text").strip()
            text_chars += len(text)
            if len(text) < 20:
                empty_pages += 1
            image_count += len(page.get_images(full=True))
        except Exception as exc:
            extraction_errors.append(f"page={index + 1}: {type(exc).__name__}: {exc}")

    sampled_pages = len(sample_indexes)
    avg_chars = round(text_chars / sampled_pages, 1) if sampled_pages else 0.0
    empty_ratio = round(empty_pages / sampled_pages, 4) if sampled_pages else 1.0
    if extraction_errors or empty_ratio >= 0.5 or avg_chars < 120:
        quality = "POOR"
    elif empty_ratio > 0.12 or avg_chars < 500:
        quality = "WARNING"
    else:
        quality = "GOOD"
    document.close()
    return {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
        "pages": page_count,
        "sampled_pages": sampled_pages,
        "avg_text_chars_per_sampled_page": avg_chars,
        "empty_sample_ratio": empty_ratio,
        "sampled_image_count": image_count,
        "text_layer_quality": quality,
        "errors": extraction_errors[:5],
    }


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app/knowledge/raw/_manual_inbox")
    rows = [inspect_pdf(path) for path in sorted(root.glob("*.pdf"))]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
