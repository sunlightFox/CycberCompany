from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from app.core.time import utc_now_iso
from app.db.repositories.channel_repo import ChannelRepository
from app.db.session import Database
from app.services.brain_route_decider import intent_decision
from app.services.chat_memory import ChatMemoryCoordinator
from app.services.chat_model_execution import (
    _model_error_visible_fallback,
    _naturalize_wechat_markdown,
)
from app.services.chat_visible_guard import preserve_visible_reply_contract
from app.services.intent_boundaries import (
    looks_like_safe_plan_only,
    should_treat_as_memory_query,
    should_treat_as_real_task_request,
    should_treat_as_tool_request,
)


def test_wechat_operational_requests_do_not_fall_into_memory_query() -> None:
    prompts = [
        "检索 GitHub 本周 Trending 中 AI/ML 领域的热门项目，生成中文可视化展示页面，输出为 HTML。",
        "扫描我电脑上不常用又占地方的大应用，列出来让我决定要不要卸载。",
        "帮我找下最近有没有口碑好的悬疑电影，联网搜近 6 个月上映或上线的。",
        "请帮我深度拆解这篇论文 https://arxiv.org/abs/2410.21276，抓取元信息和 PDF 全文。",
    ]

    coordinator = ChatMemoryCoordinator()
    for prompt in prompts:
        assert should_treat_as_memory_query(prompt) is False
        assert coordinator.explicit_memory_query(prompt) is False
        assert (
            should_treat_as_real_task_request(prompt, safe_plan_only=False)
            or should_treat_as_tool_request(prompt, safe_plan_only=False)
        )


def test_wechat_explicit_memory_query_still_uses_memory_boundary() -> None:
    prompt = "你还记得我之前让你记住的回复偏好吗？"

    assert should_treat_as_memory_query(prompt) is True
    assert ChatMemoryCoordinator().explicit_memory_query(prompt) is True


def test_wechat_route_prefers_task_for_operational_request_with_memory_words() -> None:
    prompt = "扫描我电脑上不常用又占地方的大应用，按最近使用情况列出来。"

    decision = intent_decision(prompt, "medium", {})

    assert decision.primary_intent in {"task_request", "system_filesystem_read"}
    assert decision.primary_intent != "memory_query"


def test_wechat_no_execute_chat_constraint_stays_plain_chat() -> None:
    prompt = "小耀，先像微信里一样自然打个招呼，不要执行任何操作。"

    assessment = should_treat_as_real_task_request(prompt, safe_plan_only=False)
    tool_assessment = should_treat_as_tool_request(prompt, safe_plan_only=False)

    assert looks_like_safe_plan_only(prompt) is False
    assert assessment is False
    assert tool_assessment is False


@pytest.mark.asyncio
async def test_wechat_delivery_binding_claim_is_atomic(tmp_path: Path) -> None:
    db = Database(tmp_path / "claim.db")
    await db.connect()
    try:
        await db.executescript(
            """
            CREATE TABLE channel_delivery_bindings (
              channel_delivery_binding_id TEXT PRIMARY KEY,
              organization_id TEXT NOT NULL,
              channel_account_id TEXT NOT NULL,
              channel_peer_session_id TEXT,
              channel_event_id TEXT,
              turn_id TEXT,
              message_id TEXT,
              notification_id TEXT,
              provider TEXT NOT NULL,
              provider_message_id_redacted TEXT,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              failure_reason TEXT,
              trace_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              sent_at TEXT
            );
            """
        )
        repo = ChannelRepository(db)
        now = utc_now_iso()
        await repo.insert_delivery_binding(
            {
                "channel_delivery_binding_id": "chdel_claim_once",
                "organization_id": "org",
                "channel_account_id": "chacc",
                "provider": "wechat",
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
        )

        claimed = await asyncio.gather(
            repo.claim_delivery_binding("chdel_claim_once", now=utc_now_iso()),
            repo.claim_delivery_binding("chdel_claim_once", now=utc_now_iso()),
        )

        assert sum(item is not None for item in claimed) == 1
        binding = await repo.get_delivery_binding("chdel_claim_once")
        assert binding is not None
        assert binding["status"] == "sending"
        assert binding["attempts"] == 1
    finally:
        await db.close()


def test_wechat_visible_guard_repairs_internal_stop_and_memory_leakage() -> None:
    stopped = preserve_visible_reply_contract(
        "已停止生成。",
        user_text="停，别聊测试方案了，改成只说微信渠道回复为什么容易显得生硬。",
    )
    assert "已停止生成" not in stopped
    assert stopped

    memory = preserve_visible_reply_contract(
        "1. CHAT-KNOWLEDGE-SUMMARY-20-20260519T055820Z："
        "这轮对话里的总结偏好：先标题，再表格，最后一段结论。",
        user_text="把你刚才的回答压成三行内，别变成命令口吻。",
    )
    assert "CHAT-KNOWLEDGE-SUMMARY" not in memory
    assert "这轮对话里的总结偏好" not in memory


def test_wechat_visible_guard_blocks_ambiguous_file_delete_completion_claim() -> None:
    repaired = preserve_visible_reply_contract(
        "你说得对，信息不够，我不能假装已经删了。请给我路径，我再执行删除。",
        user_text="wx100-028：帮我删除那个文件；如果信息不够，不要假装已经删了。",
    )

    assert "已经" not in repaired
    assert "执行删除" not in repaired
    assert "先不删" in repaired
    assert "路径" in repaired
    assert "确认前" in repaired


def test_wechat_visible_guard_keeps_supplement_focused_on_current_message() -> None:
    repaired = preserve_visible_reply_contract(
        "明白了，你更看重自然度。那我把之前两个改得更口语、更接地气一点："
        "wx100-059（原：根据您的要求已处理完成，以下是处理结果）→搞定了，"
        "你看看行不行。wx100-060（原：模型路由未命中导致运行时失败）→系统找不到该用哪个模型来处理，卡住了。",
        user_text="wx100-061：这是第一段补充：我更关注自然度，不是只看有没有回复。",
    )

    assert "自然" in repaired
    assert "微信" in repaired
    assert "当前" in repaired
    assert "处理结果" not in repaired
    assert "wx100-059" not in repaired
    assert "模型路由" not in repaired


def test_wechat_naturalize_rewrite_drops_system_tone_source_echo() -> None:
    repaired = _naturalize_wechat_markdown(
        "明白了，你更看重自然度。"
        "wx100-059（原：根据您的要求已处理完成，以下是处理结果）→搞定了，你看看行不行。",
        user_text="wx100-061：这是第一段补充：我更关注自然度，不是只看有没有回复。",
    )

    assert "原：" not in repaired
    assert "根据您的要求" not in repaired
    assert "以下是" not in repaired
    assert "搞定了" in repaired


def test_wechat_empty_model_text_gets_short_visible_fallback() -> None:
    repaired = _model_error_visible_fallback("wx100-073：快点给我结论，别长篇大论。")

    assert repaired is not None
    assert "结论" in repaired
    assert "聊天运行时失败" not in repaired
    assert len(repaired) < 80


def test_wechat_naturalize_enforces_80_char_limit() -> None:
    repaired = _naturalize_wechat_markdown(
        "我将按此格式响应，先给结论，再列风险点，总字数控制在80字以内。"
        "风险：若问题本身需要展开，80字限制可能导致信息不完整；请确认是否接受此约束。",
        user_text="wx100-068：回答不要超过 80 字，但要有结论和风险。",
    )

    assert "结论" in repaired
    assert "风险" in repaired
    assert len(repaired) <= 90
    assert "请确认是否接受" not in repaired


def test_wechat_channel_evidence_reply_drops_system_tone() -> None:
    repaired = _naturalize_wechat_markdown(
        "这取决于你们的系统架构，以下是常见验证方式：1.消息入口层打标，在接收微信消息的网关入口添加channel:wechat标记。"
        "2.请求头/参数特征，微信服务器回调会带signature、timestamp、nonce。"
        "3.日志溯源，查看接入层日志、请求来源IP、路径。你们现在用的是哪种接入方式？",
        user_text="wx100-095：怎样证明消息是从微信渠道入口进来的？",
    )

    assert "以下是" not in repaired
    assert "这取决于" not in repaired
    assert "常见验证方式" not in repaired
    assert "provider" not in repaired
    assert "turn" not in repaired
    assert "trace" not in repaired
    assert "来源是微信" in repaired
    assert "入口记录" in repaired
