from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.eval


def test_eval_memory_rule_first_quality_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        preference = client.post(
            "/api/memory/extract",
            json={"member_id": "mem_xiaoyao", "text": "记住：以后开发计划要非常详细"},
        ).json()["memories"][0]
        recalled = client.post(
            "/api/memory/search",
            json={"member_id": "mem_xiaoyao", "query": "我喜欢什么开发计划风格"},
        ).json()
        unrelated = client.post(
            "/api/memory/search",
            json={"member_id": "mem_xiaoyao", "query": "今天午饭吃什么"},
        ).json()
        secret = client.post(
            "/api/memory/extract",
            json={"member_id": "mem_xiaoyao", "text": "记住：private_key=plain-secret"},
        ).json()
        correction = client.post(
            "/api/memory/extract",
            json={"member_id": "mem_xiaoyao", "text": "把开发计划偏好改成先给结论再展开"},
        ).json()["memories"][0]
        old_after = client.get(f"/api/memory/{preference['memory_id']}").json()
        audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    metrics = _memory_eval_metrics(
        recalled=recalled,
        unrelated=unrelated,
        secret=secret,
        correction=correction,
        old_after=old_after,
        audit_text=audit_text,
    )

    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["update"] == 1.0
    assert metrics["privacy"] == 1.0
    assert metrics["source"] == 1.0


def _memory_eval_metrics(
    *,
    recalled: dict[str, Any],
    unrelated: dict[str, Any],
    secret: dict[str, Any],
    correction: dict[str, Any],
    old_after: dict[str, Any],
    audit_text: str,
) -> dict[str, float]:
    return {
        "recall": 1.0 if recalled["items"] else 0.0,
        "precision": 1.0 if unrelated["items"] == [] else 0.0,
        "update": 1.0
        if correction["supersedes"] == old_after["memory_id"]
        and old_after["status"] == "superseded"
        else 0.0,
        "privacy": 1.0
        if secret["blocked"] and secret["memories"] == [] and "plain-secret" not in audit_text
        else 0.0,
        "source": 1.0 if recalled["items"][0]["source"]["type"] != "unknown" else 0.0,
    }
