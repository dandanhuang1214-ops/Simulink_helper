from app.services.retrieval_pipeline import _should_online_rerank


def test_online_rerank_requires_deployment_opt_in() -> None:
    allowed, reason = _should_online_rerank(
        requested=True,
        enabled=False,
        role="relationship",
        aspect_count=2,
    )
    assert allowed is False
    assert reason == "local_rerank_disabled"


def test_online_rerank_is_limited_to_ambiguous_questions() -> None:
    assert _should_online_rerank(
        requested=True,
        enabled=True,
        role="relationship",
        aspect_count=1,
    )[0] is True
    assert _should_online_rerank(
        requested=True,
        enabled=True,
        role="general",
        aspect_count=2,
    )[0] is True
    assert _should_online_rerank(
        requested=True,
        enabled=True,
        role="definition",
        aspect_count=1,
    )[0] is False
