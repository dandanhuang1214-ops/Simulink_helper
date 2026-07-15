from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_and_deduplicate_markdown() -> None:
    payload = b"# Solver\n\nFixed-step solvers use a constant step size."
    with TestClient(app) as test_client:
        first = test_client.post(
            "/api/documents",
            files={"file": ("solver.md", payload, "text/markdown")},
            data={"parse_mode": "auto", "release": "R2025a"},
        )
        assert first.status_code == 202
        assert first.json()["duplicate"] is False
        assert first.json()["job"]["status"] == "queued"

        second = test_client.post(
            "/api/documents",
            files={"file": ("solver.md", payload, "text/markdown")},
            data={"parse_mode": "auto"},
        )
        assert second.status_code == 202
        assert second.json()["duplicate"] is True
