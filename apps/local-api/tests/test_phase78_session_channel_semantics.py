from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from test_phase54_wechat_gateway_full_link import (
    GatewayWechatClient,
    _bind_real_wechat,
    _install_fake_wechat,
    _pair_peer,
    _text_event,
)


def test_phase78_channel_session_semantics_runtime_classifies_direct_thread_and_system(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    runtime = registry.channel_session_semantics_runtime

    direct = runtime.resolve_inbound(
        provider="wechat",
        channel_account_id="chacc_phase78",
        channel_message_id="msg_phase78_dm",
        raw_payload={
            "chat_type": "private",
            "peer_ref_redacted": "sha256:peer-dm",
            "source_timestamp": "2026-05-10T00:00:00+08:00",
        },
    )
    thread = runtime.resolve_inbound(
        provider="wechat",
        channel_account_id="chacc_phase78",
        channel_message_id="msg_phase78_thread",
        raw_payload={
            "chat_type": "private",
            "peer_ref_redacted": "sha256:peer-dm",
            "thread_ref": "thread_phase78",
            "source_timestamp": "2026-05-10T00:00:01+08:00",
        },
    )
    system_event = runtime.resolve_inbound(
        provider="wechat",
        channel_account_id="chacc_phase78",
        channel_message_id="msg_phase78_system",
        raw_payload={
            "delivery_mode": "system",
            "peer_ref_redacted": "sha256:system",
        },
    )

    assert direct["delivery_mode"] == "dm"
    assert direct["session_peer_ref_redacted"] == "sha256:peer-dm"
    assert direct["channel_thread_id"] is None

    assert thread["delivery_mode"] == "thread"
    assert thread["channel_thread_id"] == "thread_phase78"
    assert thread["session_peer_ref_redacted"] != direct["session_peer_ref_redacted"]

    assert system_event["delivery_mode"] == "system"
    assert system_event["session_semantics"]["peer_scope"] == "system"
    assert system_event["cross_channel_reuse_allowed"] is False


def test_phase78_channel_ingress_runtime_writes_extended_ingress_metadata(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    captured: dict[str, Any] = {}

    async def fake_create_turn(request: Any, *, retry_of_turn_id: str | None = None) -> Any:
        captured["request"] = request
        captured["retry_of_turn_id"] = retry_of_turn_id
        return {"turn_id": "turn_phase78"}

    registry.session_runtime.create_turn = fake_create_turn

    result = _run_async(
        client,
        registry.channel_ingress_runtime.submit_channel_turn(
            provider="wechat",
            session={
                "session_id": "sess_phase78",
                "conversation_id": "conv_phase78",
                "member_id": "mem_xiaoyao",
            },
            inbound_event_id="chevt_phase78",
            channel_message_id="msg_phase78",
            text="继续这条线程",
            raw_payload={"channel_event_id": "chevt_phase78"},
            ui_mode="wechat_chat",
            channel_account_id="chacc_phase78",
            channel_peer_id_redacted="sha256:peer-phase78",
            channel_thread_id="thread_phase78",
            delivery_mode="thread",
            source_timestamp="2026-05-10T00:00:02+08:00",
            dedupe_key="sha256:dedupe-phase78",
            queue_policy="collect",
        ),
    )

    assert result["turn_id"] == "turn_phase78"
    request = captured["request"]
    assert request.ingress_metadata.channel == "wechat"
    assert request.ingress_metadata.inbound_event_id == "chevt_phase78"
    assert request.ingress_metadata.channel_account_id == "chacc_phase78"
    assert request.ingress_metadata.channel_peer_id_redacted == "sha256:peer-phase78"
    assert request.ingress_metadata.channel_thread_id == "thread_phase78"
    assert request.ingress_metadata.delivery_mode == "thread"
    assert request.ingress_metadata.source_timestamp == "2026-05-10T00:00:02+08:00"
    assert request.ingress_metadata.dedupe_key == "sha256:dedupe-phase78"
    assert request.ingress_metadata.queue_policy == "collect"


def test_phase78_wechat_gateway_uses_channel_ingress_runtime_main_path(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase78-peer-secret")
    registry = cast(Any, client.app).state.registry
    captured: list[dict[str, Any]] = []

    async def fake_submit_channel_turn(**kwargs: Any) -> Any:
        captured.append(dict(kwargs))
        return await _insert_completed_turn_from_route(
            registry,
            kwargs,
            assistant_text="七十八阶段链路已统一。",
            conversation_id="conv_phase78_gateway",
        )

    async def fail_if_old_path(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("gateway should not call chat_service.create_turn directly")

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
    registry.wechat_gateway_service._chat.create_turn = fail_if_old_path

    GatewayWechatClient.events = [
        _text_event("evt-phase78-gateway", "wxid-phase78-peer-secret", "这条消息走统一入口")
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")

    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    assert captured
    assert captured[-1]["channel_account_id"]
    assert captured[-1]["channel_peer_id_redacted"].startswith("sha256:")
    assert captured[-1]["delivery_mode"] == "dm"
    assert captured[-1]["dedupe_key"].startswith("sha256:")


def test_phase78_runtime_topology_and_readiness_expose_channel_semantics_truth(
    client: TestClient,
) -> None:
    topology = client.get("/api/system/runtime-topology").json()["items"]
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    items = {item["name"]: item for item in topology}

    assert items["channel_session_semantics"]["runtime"] == "channel_session_semantics"
    assert items["channel_session_semantics"]["status"] == "runtime_native"
    assert items["wechat_gateway"]["details"]["session_semantics_runtime"] == "channel_session_semantics"
    assert items["feishu_gateway"]["details"]["fallback_removed"] is True

    phase78 = readiness["phase_readiness"]["phase78_session_channel_semantics"]
    assert phase78["status"] == "ready"
    assert phase78["next_owner_module"] == "apps/local-api/app/services/channel_session_semantics.py"


def _run_async(client: TestClient, awaitable: Any) -> Any:
    async def runner() -> Any:
        return await awaitable

    return cast(Any, client).portal.call(runner)


async def _insert_completed_turn_from_route(
    registry: Any,
    route_kwargs: dict[str, Any],
    *,
    assistant_text: str,
    conversation_id: str,
) -> Any:
    request = registry.channel_ingress_runtime._router.route(**route_kwargs).to_turn_request()
    from test_phase54_wechat_gateway_full_link import _insert_completed_turn

    return await _insert_completed_turn(
        registry,
        request,
        assistant_text=assistant_text,
        conversation_id=conversation_id,
    )
