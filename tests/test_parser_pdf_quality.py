import json

import fitz

from app.config import get_settings
from app.services.chunker import chunk_blocks, chunk_manifest
from app.services.parser import parse_document


def _use_tmp_knowledge_root(monkeypatch, tmp_path):
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    get_settings.cache_clear()


def test_text_pdf_writes_good_quality_metadata(monkeypatch, tmp_path) -> None:
    _use_tmp_knowledge_root(monkeypatch, tmp_path)
    pdf_path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Simulink solver documentation\nFixed-step solver uses a constant step size.")
    doc.save(pdf_path)
    doc.close()

    blocks = parse_document(pdf_path, document_id=101, parse_mode="auto")
    metadata = json.loads((tmp_path / "knowledge" / "parsed" / "101" / "metadata.json").read_text(encoding="utf-8"))
    manifest = chunk_manifest(chunk_blocks(blocks))

    assert metadata["status"] in {"GOOD", "WARNING"}
    assert metadata["source_method"] == "pdf_text"
    assert blocks
    assert blocks[0].page == 1
    assert blocks[0].bbox
    assert manifest["chunks"][0]["source_method"] == "pdf_text"


def test_blank_pdf_is_marked_poor_and_ocr_pending(monkeypatch, tmp_path) -> None:
    _use_tmp_knowledge_root(monkeypatch, tmp_path)
    pdf_path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    blocks = parse_document(pdf_path, document_id=102, parse_mode="auto")
    metadata = json.loads((tmp_path / "knowledge" / "parsed" / "102" / "metadata.json").read_text(encoding="utf-8"))
    error_book = tmp_path / "knowledge" / "error-book" / "document-102-parse-quality.json"

    assert blocks == []
    assert metadata["status"] == "POOR"
    assert metadata["source_method"] == "ocr_pending"
    assert error_book.exists()
