from __future__ import annotations

import asyncio

from response_composer import (
    ComposeRequest,
    ReasoningTagFilter,
    ResponseComposer,
    strip_reasoning_tags,
)
from response_composer.opening_copy import opening_copy


def test_strip_reasoning_tags_removes_closed_and_open_blocks() -> None:
    assert strip_reasoning_tags("<think>hidden</think>\n\nvisible").strip() == "visible"
    assert strip_reasoning_tags("visible\n<think>hidden").strip() == "visible"


def test_reasoning_tag_filter_hides_split_reasoning_tags() -> None:
    delta_filter = ReasoningTagFilter()

    output = "".join(
        [
            delta_filter.feed("<thi"),
            delta_filter.feed("nk>secret"),
            delta_filter.feed("</thi"),
            delta_filter.feed("nk>\n\n大脑测试成功。"),
            delta_filter.finish(),
        ]
    )

    assert output == "大脑测试成功。"


def test_reasoning_tag_filter_preserves_visible_text_around_reasoning() -> None:
    delta_filter = ReasoningTagFilter()

    output = "".join(
        [
            delta_filter.feed("前文 <think>hidden"),
            delta_filter.feed("</think> 后文"),
            delta_filter.finish(),
        ]
    )

    assert output == "前文  后文"


def test_reasoning_tag_filter_drops_unclosed_reasoning() -> None:
    delta_filter = ReasoningTagFilter()

    output = "".join(
        [
            delta_filter.feed("<think>hidden forever"),
            delta_filter.finish(),
        ]
    )

    assert output == ""


def test_response_composer_strips_reasoning_from_response_plan() -> None:
    result = asyncio.run(
        ResponseComposer().compose(
            ComposeRequest(user_text="", result_summary="<think>hidden</think>\n\n最终答案。")
        )
    )

    assert result.text == "最终答案。"
    assert result.response_plan.plain_text == "最终答案。"


def test_response_composer_adds_conversation_voice_diagnostics() -> None:
    result = asyncio.run(
        ResponseComposer().compose(
            ComposeRequest(
                user_text="帮我把今天会议纪要压成三点，口吻自然一点。",
                result_summary="把今天的会议信息整理成三点重点，再补一个下一步。",
                scenario="direct",
                persona={"tone_hints": ["playful", "light_humor"], "mode": "playful_witty"},
                heart={"mood": "steady"},
            )
        )
    )

    voice = result.response_plan.structured_payload["conversation_voice"]
    guard = result.response_plan.structured_payload["response_quality_guard"]

    assert voice["scene"] in {"casual", "analytical"}
    assert voice["opener_family"] in {"casual", "analytical"}
    assert voice["deescalated"] is False
    assert guard["version"] == "response_quality_guard.openclaw_hermes.v4"
    assert guard["status"] == "passed"
    assert {
        "no_internal_terms",
        "no_false_done",
        "boundary_honesty",
        "privacy_redacted",
        "current_message_priority",
        "evidence_required_before_done",
        "strict_format_preserved",
        "no_mechanical_opening",
        "wechat_readability",
        "multimodal_grounded",
    }.issubset(guard["checks"])
    assert guard["visible_text_hash"].startswith("sha256:")


def test_response_composer_clarification_and_boundary_copy_sound_more_natural() -> None:
    composer = ResponseComposer()

    clarification = composer.compose_clarification(
        ["文件名或范围是什么？", "是否只读预览？"]
    )
    boundary = opening_copy("boundary.refusal", "phase69")

    assert "只读方式" in clarification
    assert "我先问清楚这几件事" not in clarification
    assert "方案铺开" in boundary
    assert "不能" in boundary
    assert "安全边界" in boundary
    assert "绕过去" not in boundary


def test_response_composer_carries_prompt_snapshot_metadata() -> None:
    result = asyncio.run(
        ResponseComposer().compose(
            ComposeRequest(
                user_text="继续刚才的话题。",
                result_summary="接上刚才的上下文，先补三个指标。",
                scenario="direct",
                channel_profile="wechat_chat",
                prompt_mode="full",
                prompt_snapshot_id="psnap_unit",
                prompt_assembly_version="chat_prompt_assembly.openclaw_hermes.v4",
                stable_prompt_hash="sha256:stable",
                dynamic_context_hash="sha256:dynamic",
                trusted_context_hash="sha256:trusted",
                untrusted_context_hash="sha256:untrusted",
                history_context_hash="sha256:history",
                current_message_hash="sha256:current",
                prompt_section_ids=[
                    "stable.soul",
                    "stable.behavior",
                    "context.untrusted",
                    "history.recent_messages",
                    "current.user_message",
                ],
                prompt_sections=[
                    {
                        "section_id": "stable.soul",
                        "layer": "stable_system",
                        "content_hash": "sha256:soul",
                    },
                    {
                        "section_id": "current.user_message",
                        "layer": "current_message",
                        "content_hash": "sha256:current",
                    },
                ],
            )
        )
    )

    payload = result.response_plan.structured_payload
    metadata = result.metadata

    assert payload["voice_policy_version"].startswith("chat_voice.")
    assert payload["prompt_mode"] == "full"
    assert payload["channel_profile"] == "wechat_chat"
    assert payload["prompt_snapshot_id"] == "psnap_unit"
    assert payload["prompt_assembly_version"] == "chat_prompt_assembly.openclaw_hermes.v4"
    assert payload["stable_prompt_hash"] == "sha256:stable"
    assert payload["dynamic_context_hash"] == "sha256:dynamic"
    assert payload["trusted_context_hash"] == "sha256:trusted"
    assert payload["untrusted_context_hash"] == "sha256:untrusted"
    assert payload["history_context_hash"] == "sha256:history"
    assert payload["current_message_hash"] == "sha256:current"
    assert payload["prompt_section_ids"][-1] == "current.user_message"
    assert all("content" not in item for item in payload["prompt_sections"])
    assert metadata["prompt_snapshot_id"] == "psnap_unit"
    assert metadata["prompt_section_ids"] == payload["prompt_section_ids"]
    assert metadata["trusted_context_hash"] == "sha256:trusted"
    assert metadata["prompt_sections"] == payload["prompt_sections"]


def test_response_composer_preserves_strict_json_on_wechat() -> None:
    result = asyncio.run(
        ResponseComposer().compose(
            ComposeRequest(
                user_text="只返回 JSON",
                result_summary='{"ok": true}',
                scenario="direct",
                channel_profile="wechat_chat",
            )
        )
    )

    guard = result.response_plan.structured_payload["response_quality_guard"]

    assert result.response_plan.plain_text == '{"ok": true}'
    assert guard["strict_format_preserved"] is True
    assert guard["checks"]["strict_format_preserved"] is True


def test_response_composer_removes_mechanical_opening() -> None:
    result = asyncio.run(
        ResponseComposer().compose(
            ComposeRequest(
                user_text="继续刚才的质量清单",
                result_summary="好的，我来继续处理。先给结论：保留三点。",
                scenario="direct",
            )
        )
    )

    guard = result.response_plan.structured_payload["response_quality_guard"]

    assert not result.response_plan.plain_text.startswith(("好的", "我来"))
    assert guard["checks"]["no_mechanical_opening"] is True


def test_response_quality_guard_flags_internal_terms_and_false_done() -> None:
    result = asyncio.run(
        ResponseComposer().compose(
            ComposeRequest(
                user_text="下载这个文件",
                result_summary="trace_id=trc_test 已下载，结果在 task_id=t_1。",
                scenario="direct",
            )
        )
    )

    guard = result.response_plan.structured_payload["response_quality_guard"]

    assert guard["status"] == "warning"
    assert guard["checks"]["no_false_done"] is False
    assert guard["checks"]["no_internal_terms"] is True
    assert "trace_id" not in result.response_plan.plain_text
    assert "task_id" not in result.response_plan.plain_text
