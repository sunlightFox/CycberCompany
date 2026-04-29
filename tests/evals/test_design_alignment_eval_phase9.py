from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.eval


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as test_client:
        yield test_client


def test_phase9_eval_design_alignment_release_suite(client: TestClient) -> None:
    suites = client.get("/api/evals/suites").json()["items"]
    suite_ids = {item["suite_id"] for item in suites}
    assert "suite_design_alignment" in suite_ids

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_design_alignment"},
    ).json()

    assert run["status"] == "passed"
    assert run["failed_cases"] == 0
