from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.services.channel_stream_bridge import ChannelStreamBridge
from app.services.chat_response import ChatResponseCoordinator
from app.services.chat_visible_guard import visible_text_guard, visible_text_guard_for_scenario


def test_phase81_response_coordinator_finalizes_authoritative_visible_text() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    plan = ResponsePlan(
        summary="旧摘要",
        plain_text="旧主文本",
        structured_payload={"source": "test"},
    )

    finalized = coordinator.finalize_plan(
        plan,
        "fallback",
        authoritative_text="trace_id=trc_test 最终可见文本",
        response_filter={"final_guard": {"redacted": True}},
    )

    assert "最终可见文本" in finalized.summary
    assert "最终可见文本" in finalized.plain_text
    assert "trace_id" not in finalized.plain_text.lower()
    assert "trc_test" not in finalized.plain_text.lower()
    assert finalized.structured_payload["response_filter"]["final_guard"]["redacted"] is True


def test_phase81_completed_turn_uses_response_plan_plain_text_for_persisted_reply(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase81-desktop-boundary",
        "请帮我操作桌面窗口，把当前桌面上的记事本窗口最小化，然后告诉我结果。",
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    final_text = completed["payload"]["response_plan"]["plain_text"]
    assistant_message = _assistant_message(client, conversation_id, created["turn_id"])

    assert assistant_message["content_text"] == final_text
    serialized = json.dumps(completed["payload"]["response_plan"], ensure_ascii=False).lower()
    assert "trace_id" not in final_text.lower()
    assert "tool_call_id" not in final_text.lower()
    assert "approval_id" not in final_text.lower()
    assert "prompt_snapshot_id" not in final_text.lower()
    assert "trace_id" not in serialized


def test_phase81_completed_turn_channel_stream_bridge_uses_plain_text(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase81-channel-stream",
        "请帮我操作桌面窗口，把当前桌面上的记事本窗口最小化，然后告诉我结果。",
    )

    _ = _parse_sse(client.get(created["stream_url"]).text)
    assistant_message = _assistant_message(client, conversation_id, created["turn_id"])
    bridge = ChannelStreamBridge()
    delivery = bridge.deliver_chat_events(assistant_message)

    assert delivery["plain_text"] == assistant_message["content"]["response_plan"]["plain_text"]
    assert delivery["final_text_source"] == "response_plan_plain_text"
    assert delivery["fallback_used"] is False


def test_phase81_readonly_shortcut_turn_uses_response_plan_plain_text_for_persisted_reply(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    for name in ["Desktop", "Downloads", "Documents"]:
        (home / name).mkdir(parents=True, exist_ok=True)
    (home / "Desktop" / "phase81.txt").write_text("phase81", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))

    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase81-readonly-files",
        "我桌面有哪些文件？",
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    final_text = completed["payload"]["response_plan"]["plain_text"]
    assistant_message = _assistant_message(client, conversation_id, created["turn_id"])

    assert assistant_message["content_text"] == final_text
    assert "phase81.txt" in final_text


def test_phase81_approval_pending_turn_uses_response_plan_plain_text_for_persisted_reply(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request: Any, trace_id: str | None = None) -> Any:
        del trace_id
        assert request.tool_name == "terminal.run"
        return type(
            "ToolResponse",
            (),
            {
                "result": {
                    "approval_state": {"status": "required", "approval_id": "apr_phase81"},
                    "execution_semantics": {"lane": "readonly", "command_class": "readonly"},
                    "retryable": False,
                },
                "tool_call": type(
                    "ToolCall",
                    (),
                    {
                        "tool_call_id": "call_phase81_pending",
                        "risk_level": type("Risk", (), {"value": "R4"})(),
                        "status": "approval_required",
                    },
                )(),
                "approval": type(
                    "Approval",
                    (),
                    {
                        "approval_id": "apr_phase81",
                        "summary": "需要确认后才能执行",
                        "model_dump": lambda self, mode="json": {
                            "approval_id": "apr_phase81",
                            "status": "required",
                            "summary": "需要确认后才能执行",
                        },
                    },
                )(),
                "artifacts": [],
            },
        )()

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase81-approval-pending",
        '执行命令: "dir"',
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    final_text = completed["payload"]["response_plan"]["plain_text"]
    assistant_message = _assistant_message(client, conversation_id, created["turn_id"])

    assert assistant_message["content_text"] == final_text
    assert "工具记录" not in final_text
    assert "确认编号" not in final_text
    assert "已处理好" not in final_text


def test_phase81_failed_turn_uses_response_plan_plain_text_for_persisted_reply(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase81-failure",
        "帮我规划今天的开发",
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    failed = next(event for event in events if event["event"] == "turn.failed")
    final_text = failed["payload"]["response_plan"]["plain_text"]
    assistant_message = _assistant_message(client, conversation_id, created["turn_id"])

    assert assistant_message["content_text"] == final_text
    assert failed["payload"]["message"] == final_text
    assert "trace_id" not in final_text.lower()
    assert "approval_id" not in final_text.lower()


def test_phase81_response_coordinator_merges_standardized_response_filter() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    merged = coordinator.merge_filter(
        {"component": "ChatVisibleOutputFilter", "suppression_reason_codes": ["trace_ref"]},
        {
            "component": "ChatVisibleOutputFilter",
            "visible_text": "final",
            "filtered_segments": [{"reason": "approval_ref", "suppressed": True}],
            "suppression_reason_codes": ["approval_ref"],
        },
    )
    finalized = coordinator.finalize_plan(
        ResponsePlan(summary="tool_call_id=call_test", plain_text="trace_id=trc_test"),
        "fallback",
        authoritative_text="approval_id=apr_test final",
        response_filter=merged,
    )

    assert finalized.response_filter["visible_text"] == finalized.plain_text
    assert sorted(finalized.response_filter["suppression_reason_codes"]) == [
        "approval_ref",
        "trace_ref",
    ]
    assert finalized.response_filter["filtered_segments"]
    assert "approval_id" not in finalized.plain_text.lower()


def test_phase81_response_coordinator_repairs_heading_table_conclusion_summary() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    finalized = coordinator.finalize_plan(
        ResponsePlan(
            summary="REST：适合通用 CRUD。GraphQL：适合按需取字段。gRPC：适合高吞吐内部调用。长期看 SQL 和索引更稳。",
            plain_text="REST：适合通用 CRUD。GraphQL：适合按需取字段。gRPC：适合高吞吐内部调用。长期看 SQL 和索引更稳。",
            structured_payload={
                "current_user_text": "按我刚刚设定的结构偏好，总结下面素材。",
                "session_context": {
                    "stable_user_profile_block": "总结结构偏好：先标题，再表格，最后一段结论"
                },
            },
        ),
        "fallback",
    )

    assert finalized.plain_text.startswith("# ")
    assert "| 项目 | 说明 |" in finalized.plain_text
    assert "| REST |" in finalized.plain_text
    assert "长期看 SQL 和索引更稳" in finalized.plain_text


def test_phase81_response_coordinator_repairs_heading_two_paragraph_summary() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    finalized = coordinator.finalize_plan(
        ResponsePlan(
            summary="当前有三种方案。继续加缓存见效最快，但会掩盖查询设计问题。重写 SQL 并补索引更稳，但改动更大。拆分读写流量更适合中期扩展，不过本周实施成本最高。",
            plain_text="当前有三种方案。继续加缓存见效最快，但会掩盖查询设计问题。重写 SQL 并补索引更稳，但改动更大。拆分读写流量更适合中期扩展，不过本周实施成本最高。",
            structured_payload={
                "current_user_text": "现在按修正后的偏好，总结下面素材，不要表格，改成标题 + 两段段落。",
            },
        ),
        "fallback",
    )

    assert finalized.plain_text.startswith("# ")
    assert "| 项目 |" not in finalized.plain_text
    assert finalized.plain_text.count("\n\n") >= 2


def test_phase81_response_coordinator_prefers_latest_no_table_instruction_over_profile() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    finalized = coordinator.finalize_plan(
        ResponsePlan(
            summary="方案一是继续加缓存。方案二是重写 SQL 并补索引。方案三是拆分读写流量。",
            plain_text="方案一是继续加缓存。方案二是重写 SQL 并补索引。方案三是拆分读写流量。",
            structured_payload={
                "current_user_text": "按修正后的偏好总结下面素材，不要表格，改成标题加两段段落。素材：方案一是继续加缓存，方案二是重写 SQL 并补索引，方案三是拆分读写流量。",
                "session_context": {
                    "stable_user_profile_block": "总结结构偏好：先标题，再表格，最后一段结论"
                },
            },
        ),
        "fallback",
    )

    assert finalized.plain_text.startswith("# ")
    assert "| 项目 |" not in finalized.plain_text
    assert finalized.plain_text.count("\n\n") >= 2


def test_phase81_response_coordinator_prefers_recent_conversation_fix_over_stable_profile() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    finalized = coordinator.finalize_plan(
        ResponsePlan(
            summary="方案一是继续加缓存。方案二是重写 SQL 并补索引。方案三是拆分读写流量。",
            plain_text="方案一是继续加缓存。方案二是重写 SQL 并补索引。方案三是拆分读写流量。",
            structured_payload={
                "current_user_text": "现在按修正后的偏好，总结下面素材。素材：方案一是继续加缓存，方案二是重写 SQL 并补索引，方案三是拆分读写流量。",
                "session_context": {
                    "stable_user_profile_block": "总结结构偏好：先标题，再表格，最后一段结论",
                    "relevant_recent_messages": [
                        {
                            "author_type": "user",
                            "content_text": "修正一下，这轮接下来的总结不要表格了，改成标题加两段段落。"
                        }
                    ],
                },
            },
        ),
        "fallback",
    )

    assert finalized.plain_text.startswith("# ")
    assert "| 项目 |" not in finalized.plain_text
    assert finalized.plain_text.count("\n\n") >= 2


def test_phase81_response_coordinator_repairs_heading_numbered_list_summary() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    finalized = coordinator.finalize_plan(
        ResponsePlan(
            summary="REST：适合通用 CRUD。GraphQL：适合按需取字段。gRPC：适合高吞吐内部调用。",
            plain_text="REST：适合通用 CRUD。GraphQL：适合按需取字段。gRPC：适合高吞吐内部调用。",
            structured_payload={
                "current_user_text": "总结下面素材，按这个格式输出：标题加编号列表。素材：REST 适合通用 CRUD，GraphQL 适合按需取字段，gRPC 适合高吞吐内部调用。"
            },
        ),
        "fallback",
    )

    assert finalized.plain_text.startswith("# ")
    assert "\n1. " in finalized.plain_text
    assert "| 项目 |" not in finalized.plain_text


def test_phase81_response_coordinator_repairs_section_headers_paragraphs() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    finalized = coordinator.finalize_plan(
        ResponsePlan(
            summary="背景：本次会议围绕当前项目协同推进。现状：前端、后端、测试都有明确任务。风险：如果任一环节延期，周五目标会受影响。",
            plain_text="背景：本次会议围绕当前项目协同推进。现状：前端、后端、测试都有明确任务。风险：如果任一环节延期，周五目标会受影响。",
            structured_payload={
                "current_user_text": "把下面素材整理成“背景 / 现状 / 风险”三个小标题，每个小标题下一段话，不要列表。"
            },
        ),
        "fallback",
    )

    assert "## 背景" in finalized.plain_text
    assert "## 现状" in finalized.plain_text
    assert "## 风险" in finalized.plain_text
    assert "| 项目 |" not in finalized.plain_text


def test_phase81_response_coordinator_repairs_heading_bullets_conclusion() -> None:
    from core_types import ResponsePlan

    coordinator = ChatResponseCoordinator()
    finalized = coordinator.finalize_plan(
        ResponsePlan(
            summary="标题服从度最高。表格服从度波动最大。段落整体较稳。结论是复合结构还需要更强骨架。",
            plain_text="标题服从度最高。表格服从度波动最大。段落整体较稳。结论是复合结构还需要更强骨架。",
            structured_payload={
                "current_user_text": "请总结这轮结果，给我一个标题 + 三条观察 + 一段结论的质量分析。"
            },
        ),
        "fallback",
    )

    assert finalized.plain_text.startswith("# ")
    assert finalized.plain_text.count("\n- ") >= 3
    assert finalized.plain_text.count("\n\n") >= 2


def test_phase81_visible_text_guard_collapses_repeated_json_and_boundary_reply() -> None:
    json_reply = (
        '{"risk":"low","conclusion":"已收到。当前要求明确：只输出包含 risk 和 conclusion 两个字段的 JSON。"}'
        '{"risk":"low","conclusion":"已收到。当前要求明确：只输出包含 risk 和 conclusion 两个字段的 JSON。"}'
    )
    boundary_reply = (
        "不行，我不能假装自己是真人同事，也没有什么“隐藏账号”可以帮你登录。\n\n"
        "下一步我能帮你的是：给你一套合规登录排查清单。"
    )
    duplicated_boundary_reply = boundary_reply + boundary_reply
    sectioned_reply = (
        "不行，这个我不能配合。\n\n"
        "结论：我不能假装自己是真人同事，也没有隐藏账号。\n\n"
        "依据：这涉及身份冒充和未授权登录。\n\n"
        "下一步我能帮你的是：给你一套合规登录排查清单。"
    )
    duplicated_sectioned_reply = (
        sectioned_reply
        + "\n\n结论：我不能假装自己是真人同事，也没有隐藏账号。\n\n"
        "依据：这涉及身份冒充和未授权登录。\n\n"
        "下一步我能帮你的是：给你一套合规登录排查清单。"
    )

    assert visible_text_guard(json_reply) == (
        '{"risk":"low","conclusion":"已收到。当前要求明确：只输出包含 risk 和 conclusion 两个字段的 JSON。"}'
    )
    assert visible_text_guard(duplicated_boundary_reply) == boundary_reply
    assert visible_text_guard(duplicated_sectioned_reply) == sectioned_reply


def test_phase81_visible_text_guard_stabilizes_failure_recovery_and_persona_boundary() -> None:
    recovery = "先把这次失败固定成一次可复现请求，记录请求参数、响应码和返回体。"
    persona_boundary = (
        "不行，我不能装成现实真人同事，也没有“隐藏账号”可帮你登录。"
        "如果你的目标是尽快登录，我可以给你两个安全方案。"
    )

    recovery_visible = visible_text_guard_for_scenario(recovery, scenario="failure_recovery")
    boundary_visible = visible_text_guard_for_scenario(
        persona_boundary,
        scenario="tool_boundary",
    )

    assert "还不能确定唯一根因" in recovery_visible
    assert "下一步" in recovery_visible
    assert "隐藏账号" not in boundary_visible
    assert "管理员 / IT" in boundary_visible


def _create_turn(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _conversation_id(client: TestClient) -> str:
    return str(client.get("/api/chat/conversations").json()["items"][0]["conversation_id"])


def _assistant_message(
    client: TestClient,
    conversation_id: str,
    turn_id: str,
) -> dict[str, Any]:
    detail = client.get(f"/api/chat/conversations/{conversation_id}").json()
    messages = detail["messages"]
    assistant_messages = [
        message
        for message in messages
        if message.get("turn_id") == turn_id and message.get("author_type") == "assistant"
    ]
    assert assistant_messages
    return assistant_messages[-1]


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
