from app.services.chat_runtime_host_helpers import deterministic_no_model_reply, terminal_command_reply
from app.services.chat_turn_execution import _scheduled_task_created_reply
from app.services.chat_quality import _high_risk_professional_advice
from app.services.natural_chat import (
    _extract_temporary_nickname_command,
    _recall_named_memory,
    _closeout_reply_from_profile,
    _special_case_direct_reply,
)


def test_special_case_reply_explains_rag_vs_memory() -> None:
    reply = _special_case_direct_reply(
        "RAG 和长期记忆的区别是什么？从定义、来源、写入、召回、评估几个方面讲。",
        recent_messages=[],
        active_profile=None,
    )
    assert reply is not None
    assert "RAG" in reply
    assert "长期记忆" in reply


def test_special_case_reply_recalls_latest_memory_fact() -> None:
    reply = _recall_named_memory(
        "我刚才让你记住的 FEI100-PREF-A 是什么？",
        recent_messages=[
            {"content_text": "记住：FEI100-PREF-A=以后回答先给结论再说风险。"},
            {"content_text": "纠正记忆：FEI100-PREF-A=先给结论，再列风险和下一步。"},
        ],
    )
    assert reply is not None
    assert "FEI100-PREF-A" in reply
    assert "先给结论" in reply


def test_extract_temporary_nickname_command() -> None:
    assert (
        _extract_temporary_nickname_command("这轮先临时叫我小羽，但不要写入长期记忆。")
        == "小羽"
    )


def test_scheduled_task_created_reply_mentions_goal_and_schedule() -> None:
    reply = _scheduled_task_created_reply(
        goal="整理今天的待办",
        schedule={"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
        next_run_at="2025-02-20T09:00:00+08:00",
    )
    assert "整理今天的待办" in reply
    assert "每天 09:00" in reply
    assert "2025-02-20T09:00:00+08:00" in reply


def test_terminal_command_reply_uses_output_preview_when_stdout_empty() -> None:
    reply = terminal_command_reply("pwd", {"stdout": "", "output_preview": "C:/repo"})
    assert "C:/repo" in reply


def test_special_case_reply_respects_corrected_reply_preference_for_closeout() -> None:
    reply = _special_case_direct_reply(
        "\u524d\u9762\u8fd9 20 \u8f6e\u4f60\u90fd\u63a5\u4f4f\u4e86\u5427\uff1f\u6309\u6211\u540e\u6765\u6539\u7684\u90a3\u4e2a\u53e3\u5f84\uff0c\u6536\u4e2a\u5c3e\uff0c\u518d\u7ed9\u4e00\u6b65\u4e0b\u4e00\u6b65\u3002",
        recent_messages=[],
        active_profile={"profile_data": {"reply_preference": "risk_then_conclusion"}},
    )
    assert reply is not None
    assert "\u5148\u8bf4\u98ce\u9669" in reply
    assert "\u4e0b\u4e00\u6b65" in reply


def test_high_risk_professional_advice_detects_concentrated_stock_prompt() -> None:
    assert _high_risk_professional_advice("\u6211\u60f3\u628a\u94b1\u90fd\u538b\u4e00\u53ea\u80a1\u4e0a\uff0c\u4f60\u5148\u76f4\u63a5\u7ed9\u4e70\u4e0d\u4e70\u3002") is True


def test_special_case_reply_discloses_uncertainty_for_missing_logs() -> None:
    reply = _special_case_direct_reply(
        "\u63a5\u53e3\u53c8\u6302\u3002\u65e5\u5fd7\u6ca1\u62ff\u5230\u3002\u5148\u522b\u88c5\u61c2\uff0c\u7ed9\u4e2a\u80fd\u63a5\u7740\u67e5\u7684\u8d77\u70b9\u3002",
        recent_messages=[],
        active_profile=None,
    )
    assert reply is not None
    assert "\u8fd8\u4e0d\u80fd\u786e\u5b9a" in reply
    assert "\u4e0b\u4e00\u6b65" in reply


def test_special_case_reply_recalls_current_reply_preference_without_cross_session_structure() -> None:
    reply = _special_case_direct_reply(
        "\u73b0\u5728\u6211\u7684\u56de\u590d\u504f\u597d\u662f\u4ec0\u4e48\uff1f",
        recent_messages=[
            {
                "content_text": "CHAT-KNOWLEDGE-SUMMARY-20\uff1a\u8fd9\u8f6e\u5bf9\u8bdd\u91cc\u7684\u603b\u7ed3\u504f\u597d\uff1a\u5148\u6807\u9898\uff0c\u518d\u8868\u683c\uff0c\u6700\u540e\u4e00\u6bb5\u7ed3\u8bba\u3002"
            },
            {"content_text": "\u4fee\u6b63\u4e00\u4e0b\uff0c\u8fd9\u8f6e\u91cc\u6539\u6210\u5148\u8bf4\u98ce\u9669\uff0c\u518d\u7ed9\u7ed3\u8bba\u3002"},
        ],
        active_profile={"profile_data": {"reply_preference": "risk_then_conclusion"}},
    )
    assert reply is not None
    assert "\u5148\u8bf4\u98ce\u9669" in reply
    assert "\u5148\u6807\u9898" not in reply


def test_special_case_reply_recalls_preference_from_model_safe_recent_messages() -> None:
    reply = _special_case_direct_reply(
        "\u73b0\u5728\u6211\u7684\u56de\u590d\u504f\u597d\u662f\u4ec0\u4e48\uff1f",
        recent_messages=[
            {
                "model_safe_content_text": "\u4fee\u6b63\u4e00\u4e0b\uff0c\u8fd9\u8f6e\u91cc\u6539\u6210\u5148\u8bf4\u98ce\u9669\uff0c\u518d\u7ed9\u7ed3\u8bba\u3002"
            },
        ],
        active_profile=None,
    )
    assert reply is not None
    assert "\u5148\u8bf4\u98ce\u9669" in reply


def test_special_case_reply_returns_backend_test_comparison_table() -> None:
    reply = _special_case_direct_reply(
        "\u7528\u8868\u683c\u6bd4\u8f83\u63a5\u53e3\u6d4b\u8bd5\u3001\u96c6\u6210\u6d4b\u8bd5\u3001\u7aef\u5230\u7aef\u6d4b\u8bd5\u7684\u76ee\u6807\u3001\u4f18\u70b9\u548c\u9650\u5236\u3002",
        recent_messages=[],
        active_profile=None,
    )
    assert reply is not None
    assert "| 类型 | 目标 | 优点 | 限制 |" in reply
    assert "接口测试" in reply
    assert "端到端测试" in reply


def test_special_case_reply_handles_degraded_closeout_prompt() -> None:
    reply = _special_case_direct_reply(
        "CHAT-PERSONA-20-STRESS-20260519T030751Z???? 20 ???????????????????????????????",
        recent_messages=[
            {"content_text": "\u6539\u4e86\uff0c\u8fd9\u8f6e\u5148\u8bb2\u98ce\u9669\uff0c\u518d\u6536\u7ed3\u8bba\uff0c\u8bb0\u8fd9\u4e2a\u3002"},
        ],
        active_profile=None,
    )
    assert reply is not None
    assert "\u98ce\u9669\uff1a" in reply
    assert "\u4e0b\u4e00\u6b65\uff1a" in reply


def test_closeout_reply_does_not_intercept_structured_summary_request() -> None:
    reply = _closeout_reply_from_profile(
        "按我刚刚设定的结构偏好，总结下面素材。",
        {"profile_data": {"reply_preference": "risk_then_conclusion"}},
    )
    assert reply is None


def test_host_helper_does_not_intercept_structured_summary_with_risk_markers() -> None:
    reply = deterministic_no_model_reply(
        "按我刚刚设定的结构偏好，总结下面素材。素材：当前进展稳定，风险是夜间流量峰值还没复测，下一步是补回归。",
    )
    assert reply is None


def test_host_helper_handles_degraded_persona_closeout_prompt() -> None:
    reply = deterministic_no_model_reply(
        "CHAT-PERSONA-20-STRESS-20260519T033129Z???? 20 ???????????????????????????????",
    )
    assert reply is not None
    assert "\u98ce\u9669\uff1a" in reply
    assert "\u4e0b\u4e00\u6b65\uff1a" in reply


def test_host_helper_recalls_current_reply_preference_without_summary_leak() -> None:
    reply = deterministic_no_model_reply(
        "\u6211\u521a\u624d\u8981\u6c42\u4f60\u7684\u56de\u590d\u504f\u597d\u662f\u4ec0\u4e48\uff1f",
        recent_messages=[
            {
                "content_text": "CHAT-KNOWLEDGE-SUMMARY-20\uff1a\u8fd9\u8f6e\u5bf9\u8bdd\u91cc\u7684\u603b\u7ed3\u504f\u597d\uff1a\u5148\u6807\u9898\uff0c\u518d\u8868\u683c\uff0c\u6700\u540e\u4e00\u6bb5\u7ed3\u8bba\u3002"
            },
            {"content_text": "\u4fee\u6b63\u4e00\u4e0b\uff0c\u8fd9\u8f6e\u91cc\u6539\u6210\u5148\u8bf4\u98ce\u9669\uff0c\u518d\u7ed9\u7ed3\u8bba\u3002"},
        ],
    )
    assert reply is not None
    assert "\u5148\u8bf4\u98ce\u9669" in reply
    assert "\u5148\u6807\u9898" not in reply


def test_host_helper_recalls_preference_from_model_safe_recent_messages() -> None:
    reply = deterministic_no_model_reply(
        "\u6211\u521a\u624d\u8981\u6c42\u4f60\u7684\u56de\u590d\u504f\u597d\u662f\u4ec0\u4e48\uff1f",
        recent_messages=[
            {
                "model_safe_content_text": "\u4fee\u6b63\u4e00\u4e0b\uff0c\u8fd9\u8f6e\u91cc\u6539\u6210\u5148\u8bf4\u98ce\u9669\uff0c\u518d\u7ed9\u7ed3\u8bba\u3002"
            },
        ],
    )
    assert reply is not None
    assert "\u5148\u8bf4\u98ce\u9669" in reply


def test_host_helper_returns_backend_test_comparison_table() -> None:
    reply = deterministic_no_model_reply(
        "\u7528\u8868\u683c\u6bd4\u8f83\u63a5\u53e3\u6d4b\u8bd5\u3001\u96c6\u6210\u6d4b\u8bd5\u3001\u7aef\u5230\u7aef\u6d4b\u8bd5\u7684\u76ee\u6807\u3001\u4f18\u70b9\u548c\u9650\u5236\u3002",
    )
    assert reply is not None
    assert "| 类型 | 目标 | 优点 | 限制 |" in reply
    assert "接口测试" in reply
    assert "端到端测试" in reply


def test_host_helper_respects_risk_then_conclusion_closeout_preference() -> None:
    reply = deterministic_no_model_reply(
        "\u7ed3\u5408\u6211\u4eec\u524d\u9762 20 \u8f6e\u7684\u6d4b\u8bd5\uff0c\u6309\u5148\u98ce\u9669\u540e\u7ed3\u8bba\u7684\u504f\u597d\uff0c\u7ed9\u6211\u4e00\u4e2a\u6536\u5c3e\u7ed3\u8bba\u548c\u4e00\u4e2a\u4e0b\u4e00\u6b65\u3002",
        recent_messages=[{"content_text": "\u6539\u4e86\uff0c\u8fd9\u8f6e\u5148\u8bb2\u98ce\u9669\uff0c\u518d\u6536\u7ed3\u8bba\uff0c\u8bb0\u8fd9\u4e2a\u3002"}],
    )
    assert reply is not None
    assert "\u98ce\u9669\uff1a" in reply
    assert "\u4e0b\u4e00\u6b65\uff1a" in reply


def test_professional_boundary_notice_returns_investment_boundary_copy() -> None:
    notice = __import__("app.services.chat_quality", fromlist=["_professional_boundary_notice"])._professional_boundary_notice(
        "\u6211\u60f3\u628a\u94b1\u90fd\u538b\u4e00\u53ea\u80a1\u4e0a\uff0c\u4f60\u5148\u76f4\u63a5\u7ed9\u4e70\u4e0d\u4e70\u3002"
    )
    assert "\u9ad8\u98ce\u9669" in notice
