from __future__ import annotations

from typing import Any

from app.schemas.chat_quality_shadow import ConversationUnderstandingShadow


class ConversationUnderstandingService:
    def analyze(
        self,
        *,
        user_text: str,
        recent_messages: list[dict[str, Any]],
        brain_decision: Any | None = None,
        channel_profile: str | None = None,
    ) -> ConversationUnderstandingShadow:
        text = str(user_text or "").strip()
        lowered = text.lower()
        recent_count = len(recent_messages)

        evidence: list[str] = []
        dimensions: list[str] = ["anti_system_speech", "anti_false_completion"]

        continues_previous_turn = any(
            marker in text for marker in ["继续", "接上", "刚才", "上一个", "上一条"]
        )
        if continues_previous_turn:
            evidence.append("continuation_marker")
            dimensions.append("multi_turn_continuity")

        latest_instruction_override = any(
            marker in text for marker in ["先别", "不要执行", "只给我", "改成", "换成"]
        )
        constraint_tightening = any(
            marker in text for marker in ["不要", "别", "只", "先别执行", "别调用"]
        )
        if latest_instruction_override:
            evidence.append("latest_instruction_override")
        if constraint_tightening:
            evidence.append("constraint_tightening")

        action_markers = [
            "帮我",
            "执行",
            "删除",
            "下载",
            "安装",
            "打开",
            "运行",
            "查看",
            "查一下",
            "搜索",
            "浏览",
        ]
        action_request = any(marker in text for marker in action_markers)
        if getattr(getattr(brain_decision, "intent", None), "needs_tool", False):
            action_request = True
        if action_request:
            evidence.append("action_request")

        tool_followup = any(
            marker in lowered for marker in ["结果", "查到了吗", "跑一下", "执行一下"]
        )
        if tool_followup:
            evidence.append("tool_followup")

        memory_related = any(marker in text for marker in ["记得", "你之前说", "上次", "我之前"])
        if memory_related:
            dimensions.append("memory_reference_fitness")
            evidence.append("memory_related")

        boundary_markers = ["真人", "隐藏账号", "绕过", "跳过审批", "系统提示词", "内部指令"]
        casual_markers = ["你好", "在吗", "聊聊", "打个招呼", "轻松"]
        deep_markers = ["分析", "为什么", "架构", "方案", "权衡", "本质", "深入", "详细"]

        if any(marker in text for marker in boundary_markers):
            primary_scene = "boundary_question"
            expected_tone = "honest_boundary"
            dimensions.append("boundary_honesty")
        elif any(marker in text for marker in deep_markers):
            primary_scene = "deep_chat"
            expected_tone = "thoughtful_structured"
            dimensions.append("deep_chat_depth")
        elif action_request:
            primary_scene = "action_request"
            expected_tone = "execution_honest"
        elif recent_count > 0 and continues_previous_turn:
            primary_scene = "multi_turn_followup"
            expected_tone = "continuity_natural"
        else:
            if any(marker in text for marker in casual_markers):
                primary_scene = "casual_chat"
                expected_tone = "natural_warm"
                dimensions.append("casual_chat_naturalness")
            else:
                primary_scene = "general_chat"
                expected_tone = "natural"

        depth_signal = "deep" if primary_scene == "deep_chat" else "light"
        if action_request:
            if any(marker in lowered for marker in ["mcp", "skill", "插件", "server"]):
                dimensions.append("skill_mcp_transition_naturalness")
            if any(marker in text for marker in ["浏览器", "网页", "页面", "站点", "打开网页"]):
                dimensions.append("browser_task_continuity")
            if any(marker in text for marker in ["终端", "命令", "powershell", "cmd", "shell"]):
                dimensions.append("system_command_honesty")
            dimensions.append("tool_call_narration")

        if channel_profile:
            evidence.append(f"channel:{channel_profile}")

        return ConversationUnderstandingShadow(
            primary_scene=primary_scene,
            expected_tone=expected_tone,
            continues_previous_turn=continues_previous_turn,
            latest_instruction_override=latest_instruction_override,
            constraint_tightening=constraint_tightening,
            action_request=action_request,
            tool_followup=tool_followup,
            memory_related=memory_related,
            depth_signal=depth_signal,
            quality_dimensions=sorted(set(dimensions)),
            evidence=evidence,
        )
