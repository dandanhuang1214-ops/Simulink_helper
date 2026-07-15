from app.services.retrieval import _fts_query


def test_fts_query_preserves_technical_terms() -> None:
    query = _fts_query("R2025a 中 find_system 怎么使用？")

    assert '"r2025a"' in query
    assert '"find_system"' in query
    assert '"怎么"' in query
