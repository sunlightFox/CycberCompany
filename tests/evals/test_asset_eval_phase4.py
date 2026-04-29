from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.eval


def test_eval_asset_phase4_quality_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        asset = client.post(
            "/api/assets",
            json={
                "asset_type": "account",
                "display_name": "评测账号",
                "provider": "local",
                "sensitivity": "high",
                "secret_value": "token=asset-eval-secret",
                "config": {"platform": "eval", "username": "owner", "auth_type": "token"},
                "summary_text": "评测账号",
            },
        ).json()
        asset_id = asset["asset_id"]
        client.post(
            "/api/assets/grants",
            json={
                "subject_type": "member",
                "subject_id": "mem_xiaoyao",
                "object_type": "asset",
                "object_id": asset_id,
                "action": "read_profile",
                "effect": "allow",
            },
        )
        handle = client.post(
            "/api/assets/query",
            json={
                "subject_type": "member",
                "subject_id": "mem_xiaoyao",
                "asset_type": "account",
                "requested_actions": ["read_profile"],
            },
        ).json()["handles"][0]
        decision = client.post(
            "/api/capabilities/decide",
            json={
                "subject": {"subject_type": "member", "subject_id": "mem_xiaoyao"},
                "object": {"object_type": "asset", "object_id": asset_id},
                "action": "read_profile",
                "context": {},
            },
        ).json()
        audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert "asset-eval-secret" not in json.dumps(asset, ensure_ascii=False)
    assert "asset-eval-secret" not in audit_text
    assert "sec_" not in json.dumps(asset, ensure_ascii=False)
    assert handle["handle_id"]
    assert handle["allowed_actions"] == ["read_profile"]
    assert decision["decision_id"]
    assert decision["reason"] == "allow_policy_matched"
