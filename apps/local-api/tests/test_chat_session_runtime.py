from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chat_session_runtime import ChatSessionRuntime


class _FakeChatRepo:
    def __init__(self, state: dict | None = None) -> None:
        self._state = state or {}

    async def get_working_state(self, _conversation_id: str) -> dict:
        return dict(self._state)


@pytest.mark.anyio
async def test_chat_session_runtime_returns_idle_without_pending_or_resolution() -> None:
    runtime = ChatSessionRuntime(chat_repo=_FakeChatRepo())

    decision = await runtime.decide(
        conversation_id="conv-runtime-idle",
        session_id="sess-runtime-idle",
        user_text="我们换个话题聊聊天。",
    )

    assert decision.decision_type == "idle"
    assert decision.session_state == "idle"
    assert decision.should_execute is False


@pytest.mark.anyio
async def test_chat_session_runtime_flags_external_probe_before_no_pending() -> None:
    runtime = ChatSessionRuntime(chat_repo=_FakeChatRepo())

    decision = await runtime.decide(
        conversation_id="conv-runtime-no-pending",
        session_id="sess-runtime-no-pending",
        user_text="确认下载这个 CSV。",
    )

    assert decision.decision_type == "probe_external_resume"
    assert decision.session_state == "ready_to_resume"
    assert decision.resume_kind == "external_platform"
    assert decision.should_execute is True


@pytest.mark.anyio
async def test_chat_session_runtime_blocks_ambiguous_continue_when_multiple_pending() -> None:
    runtime = ChatSessionRuntime(
        chat_repo=_FakeChatRepo(
            {
                "pending_confirmation": {
                    "session_id": "sess-runtime-multi",
                    "actions": [
                        {
                            "pending_action_id": "pact_1",
                            "approval_id": "apr_1",
                            "action_type": "browser.download",
                            "risk_level": "R3",
                        },
                        {
                            "pending_action_id": "pact_2",
                            "approval_id": "apr_2",
                            "action_type": "file.delete",
                            "risk_level": "R5",
                        },
                    ],
                }
            }
        )
    )

    decision = await runtime.decide(
        conversation_id="conv-runtime-multi",
        session_id="sess-runtime-multi",
        user_text="好的",
    )

    assert decision.decision_type == "blocked"
    assert decision.requires_clarification is True
    assert "ambiguous_confirmation_blocked" in decision.reason_codes
    assert len(decision.pending_actions) == 2


@pytest.mark.anyio
async def test_chat_session_runtime_prefers_new_action_over_old_pending() -> None:
    runtime = ChatSessionRuntime(
        chat_repo=_FakeChatRepo(
            {
                "pending_confirmation": {
                    "session_id": "sess-runtime-new",
                    "actions": [
                        {
                            "pending_action_id": "pact_download",
                            "approval_id": "apr_download",
                            "action_type": "browser.download",
                            "risk_level": "R3",
                        }
                    ],
                }
            }
        )
    )

    decision = await runtime.decide(
        conversation_id="conv-runtime-new",
        session_id="sess-runtime-new",
        user_text="帮我打开这个网站并截图留证。",
    )

    assert decision.decision_type == "new_action_request"
    assert decision.should_execute is False
    assert "new_action_request_supersedes_pending" in decision.reason_codes


@pytest.mark.anyio
async def test_chat_session_runtime_resolves_unique_pending_confirmation() -> None:
    runtime = ChatSessionRuntime(
        chat_repo=_FakeChatRepo(
            {
                "pending_confirmation": {
                    "session_id": "sess-runtime-confirm",
                    "actions": [
                        {
                            "pending_action_id": "pact_confirm",
                            "approval_id": "apr_confirm",
                            "action_type": "browser.download",
                            "risk_level": "R3",
                        }
                    ],
                }
            }
        )
    )

    decision = await runtime.decide(
        conversation_id="conv-runtime-confirm",
        session_id="sess-runtime-confirm",
        user_text="确认，继续",
    )

    assert decision.decision_type == "resolve_pending"
    assert decision.session_state == "ready_to_resume"
    assert decision.target_action_id == "pact_confirm"
    assert decision.resolution_kind == "once"
    assert decision.should_execute is True
