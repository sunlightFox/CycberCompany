from __future__ import annotations

from typing import Any

from core_types import ResponsePlan
from fastapi.testclient import TestClient

from app.services.channel_stream_bridge import ChannelStreamBridge
from app.services.chat_response import ChatResponseCoordinator
from app.services.chat_visible_guard import visible_text_guard


def test_phase81_response_plan_exposes_visible_and_internal_layers() -> None:
    plan = ResponsePlan(
        plain_text="visible",
        sections=[{"kind": "summary", "text": "visible"}],
        structured_payload={"response_quality_guard": {"status": "passed"}},
        response_filter={"component": "ChatVisibleOutputFilter"},
        route_semantics={"route": "browser_read_page"},
    )

    visible = plan.visible_layer_payload()
    internal = plan.internal_layer_payload()
    diagnostics = plan.layer_diagnostics()

    assert visible["plain_text"] == "visible"
    assert visible["reply_blocks"][0]["text"] == "visible"
    assert internal["response_filter"]["component"] == "ChatVisibleOutputFilter"
    assert internal["route_semantics"]["route"] == "browser_read_page"
    assert diagnostics["visible_authority"] == "response_plan_plain_text"


def test_phase81_response_coordinator_finalizes_contract_and_filter_shape() -> None:
    coordinator = ChatResponseCoordinator()
    plan = ResponsePlan(
        summary="tool_call_id=call_test trace_id=trc_test 已处理",
        plain_text="approval_id=apr_test 已处理",
        structured_payload={
            "response_quality_guard": {"status": "passed"},
            "route_semantics": {"route": "terminal_readonly_command"},
            "tool_result_context": {"status": "waiting_for_approval"},
            "prompt_assembly_version": "v4",
        },
    )

    finalized = coordinator.finalize_plan(
        plan,
        "fallback",
        authoritative_text="trace_id=trc_test 等你确认后我再执行",
        response_filter={
            "component": "ChatVisibleOutputFilter",
            "visible_text": "等你确认后我再执行",
            "filtered_segments": [{"reason": "trace_ref", "suppressed": True}],
            "suppression_reason_codes": ["trace_ref"],
        },
    )

    assert "trace_id" not in finalized.plain_text.lower()
    assert finalized.response_filter["visible_text"] == finalized.plain_text
    assert finalized.response_filter["filtered_segments"]
    assert "trace_ref" in finalized.response_filter["suppression_reason_codes"]
    assert finalized.response_quality_guard["status"] == "passed"
    assert finalized.route_semantics["route"] == "terminal_readonly_command"
    assert finalized.tool_status_semantics["status"] == "waiting_for_approval"
    assert finalized.structured_payload["action_status_semantics"]["status"] == "waiting_for_approval"
    assert finalized.prompt_contract_metadata["prompt_assembly_version"] == "v4"
    assert finalized.structured_payload["response_contract"]["visible_authority"] == "response_plan_plain_text"


def test_phase81_visible_text_guard_redacts_internal_ids_secrets_and_paths() -> None:
    text = (
        "trace_id=trc_test approval_id=apr_test tool_call_id=call_test "
        "prompt_snapshot_id=ps_001 token=abc123secret "
        "C:\\Users\\alice\\.ssh\\id_rsa"
    )

    visible = visible_text_guard(text, profile="relaxed")

    assert "trace_id" not in visible.lower()
    assert "tool_call_id" not in visible.lower()
    assert "approval_id" not in visible.lower()
    assert "prompt_snapshot_id" not in visible.lower()
    assert "abc123secret" not in visible
    assert ".ssh" not in visible.lower()


def test_phase81_channel_stream_bridge_prefers_response_plan_plain_text() -> None:
    bridge = ChannelStreamBridge()
    message = {
        "content_text": "legacy text",
        "content": {
            "response_plan": {
                "plain_text": "final visible text",
            }
        },
    }

    details = bridge.final_text_details(message)

    assert details["plain_text"] == "final visible text"
    assert details["source"] == "response_plan_plain_text"
    assert details["fallback_used"] is False


def test_phase81_runtime_topology_removes_chat_response_finalize_compat_shell(
    client: TestClient,
) -> None:
    items = {item["name"]: item for item in client.get("/api/system/runtime-topology").json()["items"]}
    response_helper = items["chat_response_helper"]
    stream_bridge = items["channel_stream_bridge"]

    assert response_helper["details"]["cleanup"]["allowed_to_grow"] is False
    assert response_helper["details"]["cleanup"]["public_shell_retained"] is True
    assert response_helper["details"]["cleanup"]["internal_compat_removed"] is True
    assert "finalize_cleanup" not in response_helper["details"]
    assert response_helper["details"]["visible_authority"] == "response_plan_plain_text"
    assert stream_bridge["details"]["final_text_source"] == "response_plan_plain_text"
