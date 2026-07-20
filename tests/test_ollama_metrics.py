from app.services.ollama import _generation_metrics


def test_generation_metrics_convert_ollama_nanoseconds_and_throughput() -> None:
    metrics = _generation_metrics(
        {
            "load_duration": 2_500_000_000,
            "prompt_eval_count": 300,
            "prompt_eval_duration": 1_500_000_000,
            "eval_count": 100,
            "eval_duration": 5_000_000_000,
            "total_duration": 9_000_000_000,
            "done_reason": "stop",
        },
        wall_ms=9050.5,
        first_token_ms=4100.25,
        prompt_chars=1800,
        requested_tokens=110,
    )

    assert metrics["load_ms"] == 2500.0
    assert metrics["first_token_ms"] == 4100.25
    assert metrics["tokens_per_second"] == 20.0
    assert metrics["prompt_tokens_per_second"] == 200.0
    assert metrics["requested_tokens"] == 110
    assert metrics["done_reason"] == "stop"
