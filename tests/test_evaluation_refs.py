from app.services.evaluation_refs import evidence_content_sha256


def test_evidence_content_sha256_is_deterministic_and_content_sensitive():
    assert evidence_content_sha256("same") == evidence_content_sha256("same")
    assert evidence_content_sha256("same") != evidence_content_sha256("different")


def test_evidence_content_sha256_preserves_exact_source_text():
    assert evidence_content_sha256("text") != evidence_content_sha256("text\n")
