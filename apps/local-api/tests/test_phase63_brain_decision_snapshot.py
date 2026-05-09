from __future__ import annotations

from app.services.brain_decision import _needs_live_skill_mcp_snapshot, _system_settings


def test_phase63_brain_decision_skips_live_skill_snapshot_for_plain_chat() -> None:
    assert not _needs_live_skill_mcp_snapshot("你好，小曜")
    assert not _needs_live_skill_mcp_snapshot("我今天有点赶，先给我一个稳一点的小建议")
    assert not _needs_live_skill_mcp_snapshot("解释 Skill 怎么配置，不要执行")


def test_phase63_brain_decision_requests_live_skill_snapshot_for_skill_or_mcp_text() -> None:
    assert _needs_live_skill_mcp_snapshot("帮我生成一个 Word 周报")
    assert _needs_live_skill_mcp_snapshot("用 Skill 生成一份项目周报")
    assert _needs_live_skill_mcp_snapshot("调用 MCP 工具查询一下项目状态")


def test_phase63_system_settings_requires_setting_action_and_target() -> None:
    assert _system_settings("切换模型设置到更稳的配置")
    assert _system_settings("请修改大脑配置，并关闭开发者设置里的调试开关")
    assert not _system_settings("用 Markdown 分三节说明真实模型质量回归怎么验收")
    assert not _system_settings("解释一下模型质量回归和聊天体验验收的区别")
