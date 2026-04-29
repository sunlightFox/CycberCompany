from __future__ import annotations

import json
from typing import cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase7_shell_preview_and_switch_do_not_mutate_business_values(
    client: TestClient,
) -> None:
    before_members = client.get("/api/members").json()["items"]
    before_names = {item["member_id"]: item["display_name"] for item in before_members}
    preview = client.post("/api/shells/switch/preview", json={"shell_id": "company"}).json()
    after_preview_names = {
        item["member_id"]: item["display_name"]
        for item in client.get("/api/members").json()["items"]
    }
    switched = client.post("/api/shells/switch", json={"shell_id": "company"}).json()
    after_switch_names = {
        item["member_id"]: item["display_name"]
        for item in client.get("/api/members").json()["items"]
    }
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)
    current = client.get("/api/shells/current").json()

    assert preview["business_values_unchanged"] is True
    assert "members.display_name" in preview["blocked_mutations"]
    assert before_names == after_preview_names == after_switch_names
    assert switched["to_shell_id"] == "company"
    assert "shell.switched" in audit_text
    assert current["constraints"]["system_menu_label"] == "系统管理"
    assert [item["label"] for item in current["constraints"]["asset_categories"]] == [
        "大脑",
        "账号",
        "钱包",
        "硬件",
        "知识库",
    ]


def test_phase7_shell_template_apply_is_traceable_and_does_not_overwrite(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    anyio.run(_rename_member, registry, "mem_xiaoyao", "用户改过的小曜")

    templates = client.get("/api/shells/company/templates").json()
    applied = client.post("/api/shells/company/templates/member_template:xiaoyao/apply").json()
    policy = client.get("/api/members/mem_xiaoyao/skill-policies").json()
    availability = client.get("/api/members/mem_xiaoyao/availability").json()
    member = next(
        item
        for item in client.get("/api/members").json()["items"]
        if item["member_id"] == "mem_xiaoyao"
    )

    assert any(item["key"] == "xiaoyao" for item in templates["templates"]["members"])
    assert applied["template_type"] == "member_template"
    assert applied["object_id"] == "mem_xiaoyao"
    assert member["display_name"] == "用户改过的小曜"
    assert "coordination" in policy["allowed_skills"]
    assert availability["status"] == "available"


async def _rename_member(registry, member_id: str, display_name: str) -> None:
    await registry.db.execute(
        "UPDATE members SET display_name = ? WHERE member_id = ?",
        (display_name, member_id),
    )
