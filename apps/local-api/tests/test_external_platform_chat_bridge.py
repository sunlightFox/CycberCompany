from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from core_types import RiskLevel
from fastapi.testclient import TestClient

from app.core.time import utc_now_iso


def test_external_platform_chat_can_approve_and_resume_human_handoff(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    registry = cast(Any, client.app).state.registry
    conversation_id = _conversation_id(client)
    _create_social_platform_target(client)
    account = _create_account(
        client,
        display_name="小红书品牌号",
        provider_key="social_xiaohongshu",
    )
    _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)

    async def fake_execute_adapter(plan_id: str, request: Any, *, trace_id: str | None = None) -> Any:
        plan_response = await registry.external_platform_action_service.get_plan(plan_id)
        await registry.external_platform_action_service._repo.update_plan(
            plan_id,
            {
                "status": "awaiting_human",
                "metadata": {
                    **dict(plan_response.plan.metadata or {}),
                    "chat_next_step": "resume_after_login",
                },
                "updated_at": utc_now_iso(),
            },
        )
        updated = await registry.external_platform_action_service.get_plan(plan_id)
        return SimpleNamespace(
            plan=updated.plan,
            message="我已经推进到登录环节。你先登录，随后直接跟我说继续。",
            next_step="resume_after_login",
        )

    async def fake_resume_after_human(plan_id: str, request: Any, *, trace_id: str | None = None) -> Any:
        plan_response = await registry.external_platform_action_service.get_plan(plan_id)
        await registry.external_platform_action_service._repo.update_plan(
            plan_id,
            {
                "status": "completed",
                "metadata": {
                    **dict(plan_response.plan.metadata or {}),
                    "chat_resumed": True,
                },
                "updated_at": utc_now_iso(),
            },
        )
        updated = await registry.external_platform_action_service.get_plan(plan_id)
        return SimpleNamespace(
            plan=updated.plan,
            message="我已经继续执行这项外部平台操作，并且流程走完了。",
            next_step=None,
        )

    monkeypatch.setattr(registry.external_platform_adapter_service, "execute_adapter", fake_execute_adapter)
    monkeypatch.setattr(
        registry.external_platform_adapter_service,
        "resume_after_human",
        fake_resume_after_human,
    )
    import asyncio

    assert asyncio.run(
        registry.external_platform_action_service.looks_like_chat_request(
            "帮我在小红书发布内容，正文：这是聊天桥接回归测试。"
        )
    )

    created = _chat(
        client,
        conversation_id,
        "ext-bridge",
        "帮我在小红书发布内容，正文：这是聊天桥接回归测试。",
    )
    working = client.get(f"/api/chat/conversations/{conversation_id}/working-state").json()
    created_detail = created["detail"]
    created_response_plan = created_detail.get("response_plan", {}) or _response_plan_from_events(
        created["events"]
    )
    pending_actions = list(
        (
            working.get("pending_confirmation", {}).get("actions")
            or created_response_plan.get("structured_payload", {}).get("pending_actions")
            or []
        )
    )
    assert pending_actions, {
        "reply": created["reply"],
        "working": working,
        "response_plan": created_response_plan,
    }
    pending = pending_actions[0]
    created_payload = created_response_plan.get("structured_payload", {})
    reply_options = (
        created_payload.get("natural_reply_options")
        or working.get("natural_reply_options")
        or working.get("pending_confirmation", {}).get("questions")
        or []
    )

    assert created["status"] == "completed"
    assert pending["action_type"] == "external_platform.publish_content"
    assert reply_options
    assert "审批" in created["reply"] or "等待" in created["reply"]
    assert asyncio.run(
        registry.chat_service._natural_chat._pending_actions(
            conversation_id,
            "ext-bridge",
            user_text="确认，继续",
        )
    )

    approved = _chat(client, conversation_id, "ext-bridge", "确认，继续")
    detail = approved["detail"]
    payload = (
        detail.get("response_plan", {}) or _response_plan_from_events(approved["events"])
    ).get("structured_payload", {})

    assert approved["status"] == "completed", json.dumps(
        {
            "reply": approved["reply"],
            "detail": detail,
            "events": approved["events"],
            "payload": payload,
        },
        ensure_ascii=False,
        default=str,
    )
    assert "登录" in approved["reply"] or "继续" in approved["reply"]
    assert payload.get("external_platform_action") is True

    resumed = _chat(client, conversation_id, "ext-bridge", "已登录，继续")
    resumed_detail = resumed["detail"]
    resumed_payload = (
        resumed_detail.get("response_plan", {}) or _response_plan_from_events(resumed["events"])
    ).get("structured_payload", {})

    assert resumed["status"] == "completed"
    assert "继续执行" in resumed["reply"] or "走完" in resumed["reply"] or "办完" in resumed["reply"]
    assert resumed_payload.get("external_platform_action") is True
    assert resumed_payload.get("external_platform_plan", {}).get("status") == "completed"


def _chat(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    stream = client.get(body["stream_url"])
    assert stream.status_code == 200, stream.text
    detail = client.get(f"/api/chat/turns/{body['turn_id']}")
    events = client.get(f"/api/chat/turns/{body['turn_id']}/events")
    assert detail.status_code == 200, detail.text
    assert events.status_code == 200, events.text
    reply = _extract_stream_text(stream.text) or _extract_events_text(events.json()["items"])
    return {
        "turn_id": body["turn_id"],
        "status": detail.json()["status"],
        "reply": reply,
        "detail": detail.json(),
        "events": events.json()["items"],
    }


def _conversation_id(client: TestClient) -> str:
    return str(client.get("/api/chat/conversations").json()["items"][0]["conversation_id"])


def _extract_stream_text(text: str) -> str:
    chunks: list[str] = []
    completed_text = ""
    for block in text.split("\n\n"):
        lines = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if not lines:
            continue
        try:
            event = json.loads("\n".join(lines))
        except json.JSONDecodeError:
            continue
        if event.get("event") == "response.delta":
            chunks.append(str(event.get("payload", {}).get("text", "")))
        if event.get("event") == "response.completed":
            payload = event.get("payload", {}) or {}
            response_plan = payload.get("response_plan", {}) or {}
            completed_text = str(
                response_plan.get("plain_text") or response_plan.get("summary") or completed_text
            )
    return "".join(chunks) or completed_text


def _extract_events_text(items: list[dict[str, Any]]) -> str:
    text_chunks: list[str] = []
    completed_text = ""
    for item in items:
        payload = item.get("payload", {}) if isinstance(item, dict) else {}
        inner = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else payload
        event = str(item.get("event") or payload.get("event") or item.get("event_type") or "")
        if event == "response.delta":
            text_chunks.append(str(inner.get("text") or ""))
        elif event == "response.completed":
            response_plan = inner.get("response_plan", {}) or {}
            completed_text = str(
                response_plan.get("plain_text") or response_plan.get("summary") or completed_text
            )
    return "".join(text_chunks) or completed_text


def _response_plan_from_events(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in reversed(items):
        payload = item.get("payload", {}) if isinstance(item, dict) else {}
        inner = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else payload
        event = str(item.get("event") or payload.get("event") or item.get("event_type") or "")
        if event == "response.completed":
            return dict(inner.get("response_plan") or {})
    return {}


def _create_social_platform_target(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/targets",
        json={
            "platform_key": "social_xiaohongshu",
            "display_name": "小红书",
            "aliases": ["小红书", "xhs", "rednote"],
            "supported_actions": ["publish_content", "read_status"],
            "required_asset_types": ["account"],
            "execution_modes": ["browser"],
            "risk_defaults": {"publish_content": "R4", "read_status": "R1"},
            "metadata": {
                "test_social_platform_target": True,
                "real_external_platform_integration": False,
            },
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_account(
    client: TestClient,
    *,
    display_name: str,
    provider_key: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": display_name,
            "provider": provider_key,
            "sensitivity": "high",
            "config": {
                "platform": provider_key,
                "username": display_name,
                "auth_type": "token",
            },
            "secret_value": "token=ext-chat-secret",
            "owner_scope_type": "member",
            "owner_scope_id": "mem_xiaoyao",
            "visibility": "private",
            "risk_level": "R4",
            "summary_text": f"{display_name} external platform account",
            "capabilities": ["login", "publish_content", "publish_post"],
            "metadata": {"platform": provider_key, "label": display_name},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _grant(client: TestClient, asset_id: str, action: str, risk: RiskLevel) -> dict[str, Any]:
    response = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": action,
            "effect": "allow",
            "risk_level": risk.value,
            "source_type": "external_platform_chat_test",
            "source_id": asset_id,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())
