from __future__ import annotations

from app.services.brain_decision import _needs_live_skill_mcp_snapshot


def test_phase63_brain_decision_skips_live_skill_snapshot_for_plain_chat() -> None:
    assert not _needs_live_skill_mcp_snapshot("你好，小曜")
    assert not _needs_live_skill_mcp_snapshot("我今天有点赶，先给我一个稳一点的小建议")
    assert not _needs_live_skill_mcp_snapshot("解释 Skill 怎么配置，不要执行")


def test_phase63_brain_decision_requests_live_skill_snapshot_for_skill_or_mcp_text() -> None:
    assert _needs_live_skill_mcp_snapshot("帮我生成一个 Word 周报")
    assert _needs_live_skill_mcp_snapshot("用 Skill 生成一份项目周报")
    assert _needs_live_skill_mcp_snapshot("调用 MCP 工具查询一下项目状态")
