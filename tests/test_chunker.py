from app.services.chunker import CHUNKING_VERSION, chunk_blocks, chunk_manifest, estimate_tokens
from app.services.parser import ParsedBlock


def test_chunker_preserves_table_as_atomic_block() -> None:
    blocks = [
        ParsedBlock("第一段", heading_path=["Solver"]),
        ParsedBlock("| A | B |\n| --- | --- |\n| 1 | 2 |", block_type="table", heading_path=["Solver"]),
        ParsedBlock("第二段", heading_path=["Solver"]),
    ]

    chunks = chunk_blocks(blocks, target_tokens=20, max_tokens=40, overlap_tokens=5)

    assert any(chunk.block_type == "table" and "| 1 | 2 |" in chunk.content for chunk in chunks)
    assert all(chunk.heading_path == ["Solver"] for chunk in chunks)


def test_chunker_splits_on_metadata_change() -> None:
    blocks = [
        ParsedBlock("page one", page=1, bbox=[0, 0, 10, 10]),
        ParsedBlock("page two", page=2, bbox=[0, 0, 10, 10]),
    ]

    chunks = chunk_blocks(blocks)

    assert [chunk.page for chunk in chunks] == [1, 2]


def test_overlap_stays_inside_same_heading() -> None:
    blocks = [
        ParsedBlock("第一句内容。第二句内容。第三句内容。第四句内容。", heading_path=["A"]),
        ParsedBlock("新章节内容。", heading_path=["B"]),
    ]

    chunks = chunk_blocks(blocks, target_tokens=10, max_tokens=14, overlap_tokens=5)
    first_heading = [chunk for chunk in chunks if chunk.heading_path == ["A"]]
    second_heading = [chunk for chunk in chunks if chunk.heading_path == ["B"]]

    assert len(first_heading) >= 2
    assert second_heading[0].content == "新章节内容。"
    assert "第四句内容" not in second_heading[0].content


def test_manifest_records_rebuild_parameters() -> None:
    drafts = chunk_blocks([ParsedBlock("固定步长适合实时系统。", heading_path=["Solver"])])
    manifest = chunk_manifest(drafts)

    assert manifest["version"] == CHUNKING_VERSION
    assert manifest["target_tokens"] == 600
    assert drafts[0].estimated_tokens == estimate_tokens(drafts[0].content)
