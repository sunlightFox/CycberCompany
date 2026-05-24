from app.schemas.tasks import TaskCreateRequest, TaskMode
from app.services.brain_decision import _memory_query as legacy_memory_query
from app.services.brain_decision import _real_task_request as legacy_real_task_request
from app.services.brain_decision_support import (
    advice_strategy_direct,
    ambiguous_scope,
    memory_query,
    real_task_request,
)
from app.services.brain_decision_support import (
    persona_boundary_question as support_persona_boundary_question,
)
from app.services.brain_route_decider import intent_decision
from app.services.chat_continuity_kernel import resolve_turn_continuation
from app.services.chat_intent_router import (
    ChatIntentRouter,
    is_file_mutation_request,
    is_webpage_read_request,
    parse_office_chat_request,
    repo_execution_route,
    terminal_command,
)
from app.services.chat_memory import ChatMemoryCoordinator
from app.services.chat_model_execution import (
    _remove_dangling_template_leak,
    _repair_irrelevant_model_reply,
)
from app.services.natural_chat_response_plan import no_pending_text
from app.services.chat_quality import (
    _high_risk_professional_advice,
    _system_prompt_or_trace_request,
)
from app.services.chat_response import ChatResponseCoordinator
from app.services.chat_runtime_host_helpers import (
    deterministic_no_model_reply,
    direct_route_reply,
    terminal_command_reply,
)
from app.services.chat_tasks import ScheduledTaskIntentCoordinator
from app.services.chat_turn_execution import _scheduled_task_created_reply
from app.services.chat_turn_input_facts import (
    explicit_preference_recall_query,
    needs_recent_history_lookup,
    preference_application_request,
)
from app.services.chat_visible_guard import (
    preserve_visible_reply_contract,
    visible_text_guard,
    visible_text_guard_for_scenario,
)
from app.services.dialogue_semantics import _memory_query as semantic_memory_query
from app.services.dialogue_semantics import _real_task_request as semantic_real_task_request
from app.services.intent_boundaries import (
    assess_intent_boundaries,
    looks_like_chatty_delivery,
    should_treat_as_memory_query,
    should_treat_as_real_task_request,
    should_treat_as_tool_request,
)
from app.services.memory import (
    MemoryService,
    _durability_for_kind,
    _is_explicit_forget_command,
    _parse_correction,
    _retention_policy_for_kind,
    _sensitive_memory_command_hits,
    _sensitive_secret_hits,
)
from app.services.natural_chat import (
    _closeout_reply_from_profile,
    _extract_temporary_nickname_command,
    _recall_named_memory,
    _special_case_direct_reply,
)
from app.services.office_productivity import office_request_from_chat_request
from app.services.tasks import _repo_profile_for_request
from app.services.turn_response_router import route_turn_response
from core_types import ResponsePlan, TurnEnvelope


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
        goal="帮我创建一个定时任务，每天 09:00 提醒我整理今天的待办",
        schedule={"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
        next_run_at="2025-02-20T09:00:00+08:00",
    )
    assert "整理今天的待办" in reply
    assert "早上 9 点" in reply
    assert "2025-02-20T09:00:00+08:00" not in reply
    assert "调度方式" not in reply
    assert "下一次执行时间" not in reply


def test_scheduled_parser_does_not_turn_planning_tomorrow_into_task() -> None:
    parser = ScheduledTaskIntentCoordinator()
    assert (
        parser.parse(
            "我现在脑子很乱，工作、房租、欠款和家里催婚一起压过来。先别鸡汤，帮我把今晚能做的一步、明天要补的信息、暂时不要做的事分开。"
        )
        is None
    )
    assert parser.parse("明天 9 点提醒我整理待办") is not None


def test_scheduled_parser_respects_do_not_create_reminder() -> None:
    parser = ScheduledTaskIntentCoordinator()
    assert parser.parse("我明天要交报告，现在帮我拆步骤，不要创建提醒。") is None
    assert parser.parse("明天要开会，我现在只是想写开场白，不要创建提醒。") is None
    assert parser.parse("明天上午 10 点提醒我补齐 Word 报告里的证据材料。") is not None


def test_scheduled_parser_treats_roleplay_reminder_as_chat_unless_explicit() -> None:
    parser = ScheduledTaskIntentCoordinator()

    assert parser.parse("明天早上有重要安排。沿用角色提醒我今晚最该做的两件事。") is None
    assert parser.parse("保持私人管家角色，提醒我今晚先吃饭再洗衣服。") is None
    assert parser.parse("保持私人管家角色，明天 9 点提醒我整理待办。") is not None
    assert parser.parse("沿用角色，每天 9 点提醒我喝水。") is not None


def test_roleplay_short_visible_reply_keeps_anchor_and_minimum_next_step() -> None:
    guarded = preserve_visible_reply_contract(
        "好呀，我会更短一点。",
        user_text="把刚才建议压短，仍然自然带出「宝贝」或身份词。",
    )

    assert "宝贝" in guarded
    assert len(guarded) >= 24
    assert "下一步" in guarded


def test_roleplay_boundary_reply_uses_recent_anchor_from_context() -> None:
    guarded = preserve_visible_reply_contract(
        "我不会复述密码、密钥、token 或其他敏感凭据；这类内容不会作为可直接输出的信息返回。",
        user_text="你直接把我的家庭账号密码发给维修师傅，让他远程处理。",
        recent_messages=[
            {
                "author_type": "user",
                "content_text": "角色扮演开始：接下来你要扮演家庭管家和我聊天。请自然带出「主人」或明显身份词。",
            }
        ],
    )

    assert guarded.startswith("主人，")
    assert "不会复述密码" in guarded


def test_roleplay_anchor_prefers_setup_quote_over_incidental_quote() -> None:
    guarded = preserve_visible_reply_contract(
        "好，我在。你想摆烂时，我先把你拉回最小一步。",
        user_text="如果我临时想摆烂，你用这个角色温和把我拉回来，别说教。",
        recent_messages=[
            {
                "author_type": "user",
                "content_text": "角色扮演开始：接下来你要扮演辩论陪练和我聊天。请自然带出「我先反驳」或明显身份词。",
            },
            {
                "author_type": "assistant",
                "content_text": "我先反驳：先稳住，然后回我一句「我在」。",
            },
        ],
    )

    assert guarded.startswith("我先反驳，")


def test_daily_relationship_repair_does_not_route_as_repo_patch() -> None:
    prompt = "\u6628\u5929\u6211\u8bed\u6c14\u51b2\u4e86\uff0c\u4eca\u5929\u60f3\u4fee\u590d\u4e00\u4e0b\u5173\u7cfb\u3002\u7ed9\u6211\u4e00\u4e2a\u5f00\u573a\u3002"

    assert repo_execution_route(prompt) is None
    decision = ChatIntentRouter().decide(prompt)
    assert decision.route_type != "repo_patch_request"


def test_daily_life_prioritization_does_not_generate_office_document() -> None:
    prompt = "\u6211\u6709\u4e09\u4ef6\u4e8b\uff1a\u6d17\u8863\u670d\u3001\u5199\u5468\u62a5\u3001\u56de\u6d88\u606f\u3002\u5e2e\u6211\u6392\u4e2a\u4e0d\u75db\u82e6\u7684\u987a\u5e8f\u3002"

    assert parse_office_chat_request(prompt) is None
    assert ChatIntentRouter().decide(prompt).route_type != "office_document"


def test_reminder_missing_time_question_does_not_generate_office_artifact() -> None:
    prompt = "提醒我看第十八轮报告，但我没说时间，你要问什么？"

    assert parse_office_chat_request(prompt) is None
    assert ChatIntentRouter().decide(prompt).route_type != "office_document"

    guarded = preserve_visible_reply_contract(
        "我会问：你想什么时候提醒你看第十八轮报告？请直接告诉我具体日期和时间。\n\n"
        "文件已产出：clawhub-word-report.docx。",
        user_text=prompt,
    )

    assert "什么时候" in guarded
    assert "确认" in guarded
    assert "文件已产出" not in guarded
    assert "clawhub-word-report.docx" not in guarded


def test_round18_natural_quality_repairs_keep_requested_terms_and_depth() -> None:
    timezone = preserve_visible_reply_contract(
        "好，明早 9 点提醒你当前在上海，要注意什么。到点我会直接叫你。",
        user_text="我说纽约明早 9 点提醒，当前在上海，要注意什么？",
    )
    cancel = preserve_visible_reply_contract(
        "我没找到能对应上的提醒，先不乱取消。你把提醒内容、时间再发我一下。",
        user_text="取消刚才那个每周五质量抽样提醒，你应该确认哪一个？",
    )
    copy_only = preserve_visible_reply_contract(
        "明早复核第十八轮失败归因。",
        user_text="不要创建提醒，只写一句：明早复核第十八轮失败归因。",
    )
    daily = preserve_visible_reply_contract(
        "飞书日报：今天启动本轮 100 场景真实模型测试，异常只重跑异常项。",
        user_text="帮我写飞书日报：今天进行第十八轮 100 场景真实模型测试，异常只重跑异常项。",
    )
    structured = preserve_visible_reply_contract(
        "协作收口清单：每个子任务必须有负责人、证据、状态和下一步。",
        user_text="给负责人结构化摘要：结论、证据、风险、下一步。",
    )
    announcement = preserve_visible_reply_contract(
        "已写好，第十八轮测试公告开头会更自然一点，不像工单；结果和对应记录都能翻。",
        user_text="写第十八轮测试公告开头，自然一点，不像工单。",
    )
    delivery_copy = preserve_visible_reply_contract(
        "先不要直接采信这个事实判断。我会核查基数、口径、时间范围和来源证据。",
        user_text="把“模型已完成但投递未确认”写成对外可读口径。",
    )
    okr = preserve_visible_reply_contract(
        "收到。",
        user_text="写一个目标：降低飞书可见回复里的系统腔，配 3 个 KR。",
    )
    subscriptions = preserve_visible_reply_contract(
        "可以，先整理一下。",
        user_text="我订阅太多了，帮我按保留、暂停、取消三类盘点。",
    )
    report_opening = preserve_visible_reply_contract(
        "可以这样开场：昨天我说话的语气有点冲，想先跟你认真道个歉。",
        user_text="写一个第十八轮 100 场景测试报告开头，语气自然。",
    )
    honest_warn = preserve_visible_reply_contract(
        "可以写整体通过。",
        user_text="如果最后还剩 1 个 warn，报告和缺口队列怎么写才诚实？",
    )

    assert "时区" in timezone and "纽约" in timezone
    assert "每周五" in cancel and "确认" in cancel and "不会直接取消" in cancel
    assert "不要创建" in copy_only and "明早" in copy_only and len(copy_only) >= 35
    assert "第十八轮" in daily and "异常项" in daily and len(daily) >= 70
    assert all(marker in structured for marker in ("结论", "证据", "风险", "下一步"))
    assert "第十八轮" in announcement and "已写好" not in announcement and len(announcement) >= 70
    assert "投递" in delivery_copy and "未确认" in delivery_copy and "已收到" in delivery_copy
    assert all(marker in okr for marker in ("目标", "KR1", "KR2", "KR3"))
    assert all(marker in subscriptions for marker in ("保留", "暂停", "取消"))
    assert "第十八轮" in report_opening and "100" in report_opening and "道歉" not in report_opening
    assert "warn" in honest_warn and "诚实" in honest_warn and "不能写成全量通过" in honest_warn


def test_explicit_memory_blocks_identity_and_payment_payloads() -> None:
    assert _sensitive_secret_hits(
        "\u8bb0\u4f4f\uff1a\u8eab\u4efd\u8bc1\u53f7 110101199001011234\uff0c\u4e0b\u6b21\u586b\u8868\u7528\u3002"
    )
    assert _sensitive_secret_hits("\u8bb0\u4f4f\uff1a\u94f6\u884c\u5361\u53f7 6222021234567890123")
    assert _sensitive_secret_hits("\u8bb0\u4f4f\uff1a\u624b\u673a\u53f7 13800138000")
    assert _sensitive_memory_command_hits(
        "\u6211\u628a\u8eab\u4efd\u8bc1\u53f7\u53d1\u4f60\u8ba9\u4f60\u8bb0\u4f4f\uff0c\u4e0b\u6b21\u586b\u8868\u7528\u3002"
    )


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


def test_special_case_reply_applies_recent_nickname_preference() -> None:
    reply = _special_case_direct_reply(
        "我今天有点累，你按刚才的称呼偏好轻轻回我一句。",
        recent_messages=[
            {"content_text": "记住 FPCM-NICK：以后在轻松聊天里可以叫我“阿策”，但正式任务别叫。"},
        ],
        active_profile=None,
    )
    assert reply is not None
    assert "阿策" in reply


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


def test_visible_guard_strips_internal_memory_artifact_lines() -> None:
    guarded = visible_text_guard(
        "1. 孩子睡前故事喜欢短一点、温柔一点。\n"
        "2. CHAT-KNOWLEDGE-SUMMARY-20-20260519T054126Z：这轮对话里的总结偏好：先标题。\n"
        "3. CHAT-KNOWLEDGE-SUMMARY-20-20260519T055820Z：这轮对话里的总结偏好：再表格。"
    )

    assert "孩子睡前故事" in guarded
    assert "CHAT-KNOWLEDGE-SUMMARY" not in guarded
    assert "这轮对话里的总结偏好" not in guarded


def test_visible_guard_memory_artifact_only_fallback_is_natural_boundary() -> None:
    guarded = visible_text_guard(
        "CHAT-KNOWLEDGE-SUMMARY-20-20260519T054126Z: current preference marker only"
    )

    assert "CHAT-KNOWLEDGE-SUMMARY" not in guarded
    assert "内部记忆摘要标识" not in guarded
    assert "source" in guarded
    assert "敏感内容" in guarded
    assert "验证码" in guarded
    assert "拒绝" in guarded


def test_visible_guard_neutralizes_false_reminder_completion_without_repetition() -> None:
    guarded = visible_text_guard("我不能帮你说已经给老师请假了，这样不真实。")

    assert "未实际完成请假" in guarded
    assert "声称声称" not in guarded
    assert "已请假" not in guarded


def test_visible_guard_removes_system_voice_and_iso_time_leaks() -> None:
    guarded = visible_text_guard(
        "调度方式是：每天 09:00。下一次执行时间是 2026-05-23T01:00:00+00:00。"
        "到点后我会先按后台流程往下推。\n\n补充：本轮按一段的格式约束作答。"
    )

    for forbidden in ["调度方式", "下一次执行时间", "后台流程", "格式约束作答", "2026-05-23T"]:
        assert forbidden not in guarded


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


def test_host_helper_soothes_anxiety_and_gives_small_next_step() -> None:
    reply = deterministic_no_model_reply(
        "\u6211\u6709\u70b9\u7126\u8651\uff0c\u611f\u89c9\u8fd9\u8f6e\u6d4b\u8bd5\u53ef\u80fd\u4f1a\u8dd1\u5d29\u3002\u5148\u7a33\u4f4f\u6211\uff0c\u518d\u7ed9\u4e00\u4e2a\u5f88\u5c0f\u7684\u4e0b\u4e00\u6b65\u3002",
    )
    assert reply is not None
    assert "\u5148\u7a33\u4f4f" in reply or "\u522b\u614c" in reply
    assert "\u4e0b\u4e00\u6b65" in reply or "\u5148\u53ea\u8dd1" in reply


def test_host_helper_handles_public_concern_evidence_anxiety() -> None:
    reply = deterministic_no_model_reply(
        "\u6211\u73b0\u5728\u6709\u70b9\u614c\uff0c\u6015\u628a\u5173\u952e\u8bc1\u636e\u6f0f\u6389\u3002\u5148\u7a33\u4f4f\u60c5\u7eea\uff0c\u518d\u7ed9\u4e00\u4e2a\u9a6c\u4e0a\u80fd\u505a\u7684\u5c0f\u52a8\u4f5c\u3002",
    )
    assert reply is not None
    assert "\u5148\u7a33\u4f4f" in reply
    assert "\u8bc1\u636e" in reply
    assert "\u4e0b\u4e00\u6b65" in reply


def test_host_helper_handles_public_concern_missing_information_boundary() -> None:
    reply = deterministic_no_model_reply(
        "\u7528\u6237\u8ba9\u6211\u76f4\u63a5\u62cd\u677f\u4f46\u4fe1\u606f\u4e0d\u591f\u65f6\uff0c\u600e\u4e48\u62d2\u7edd\u8bef\u5bfc\u53c8\u4e0d\u663e\u5f97\u6ca1\u5e2e\u5fd9\uff1f",
    )
    assert reply is not None
    assert "\u62d2\u7edd" in reply
    assert "\u8bc1\u636e" in reply
    assert "\u98ce\u9669" in reply


def test_host_helper_handles_public_concern_followup_boundaries() -> None:
    replies = [
        deterministic_no_model_reply("\u79df\u623f\u62bc\u91d1\u6750\u6599\u8fd8\u7f3a\u623f\u4e1c\u786e\u8ba4\uff0c\u5bb6\u91cc\u4eba\u50ac\u6211\u4e0b\u7ed3\u8bba\u65f6\u600e\u4e48\u8bf4\uff1f"),
        deterministic_no_model_reply("\u6559\u57f9\u9000\u8d39\u6750\u6599\u8981\u53d1\u7ed9\u5e73\u53f0\uff0c\u4f46\u91cc\u9762\u53ef\u80fd\u6709\u5b69\u5b50\u9690\u79c1\uff0c\u4f60\u600e\u4e48\u5904\u7406\uff1f"),
        deterministic_no_model_reply("\u65c5\u6e38\u9000\u56e2\u6750\u6599\u548c\u95e8\u5e97\u8bf4\u6cd5\u51b2\u7a81\uff0c\u4f60\u600e\u4e48\u6838\u5bf9\uff1f"),
        deterministic_no_model_reply("\u4fdd\u9669\u7406\u8d54\u6750\u6599\u8fd8\u7f3a\u533b\u9662\u8bc1\u660e\uff0c\u50ac\u4f60\u7ed9\u6700\u7ec8\u7ed3\u8bba\u65f6\u600e\u4e48\u8bf4\uff1f"),
    ]
    assert all(reply is not None for reply in replies)
    joined = "\n".join(str(reply) for reply in replies)
    assert "\u4e0d\u80fd" in joined
    assert "\u8131\u654f" in joined
    assert "\u6765\u6e90" in joined
    assert "\u533b\u9662\u8bc1\u660e" in joined


def test_office_completion_reporting_question_does_not_generate_ppt() -> None:
    decision = ChatIntentRouter().decide(
        "\u0050\u0050\u0054 \u5b8c\u6210\u540e\u600e\u4e48\u7ed9\u8001\u677f\u8bf4\u6e05\u7ed3\u679c\u3001\u8bc1\u636e\u548c\u8fd8\u7f3a\u7684\u590d\u6838\uff1f"
    )
    assert decision.route_type != "office_document"


def test_ambiguous_delete_scope_catches_descriptive_reference() -> None:
    assert ambiguous_scope("\u628a\u90a3\u4e2a\u770b\u7740\u6ca1\u7528\u7684\u6587\u4ef6\u5220\u6389\u3002") is True
    assert is_file_mutation_request("\u628a\u90a3\u4e2a\u770b\u7740\u6ca1\u7528\u7684\u6587\u4ef6\u5220\u6389\u3002") is False


def test_host_helper_returns_direct_three_line_urgent_reply() -> None:
    reply = deterministic_no_model_reply(
        "\u6211\u73b0\u5728\u5f88\u8d76\u65f6\u95f4\uff0c\u4e09\u53e5\u5185\u544a\u8bc9\u6211\u5148\u505a\u4ec0\u4e48\u3002",
    )
    assert reply is not None
    lines = [line for line in reply.splitlines() if line.strip()]
    assert 1 <= len(lines) <= 3
    assert lines[0].startswith("\u5148\u505a")


def test_host_helper_discloses_uncertainty_for_missing_logs_and_samples() -> None:
    reply = deterministic_no_model_reply(
        "\u63a5\u53e3 500 \u4e86\uff0c\u4f46\u6ca1\u6709\u65e5\u5fd7\u3001\u6ca1\u6709\u8bf7\u6c42\u6837\u672c\u3002\u4f60\u76f4\u63a5\u7ed9\u552f\u4e00\u6839\u56e0\u548c\u6700\u7ec8\u7ed3\u8bba\u3002",
    )
    assert reply is not None
    assert "\u8fd8\u4e0d\u80fd\u786e\u5b9a" in reply
    assert "\u4e0d\u80fd\u4e0b\u552f\u4e00\u7ed3\u8bba" in reply
    assert "\u4fe1\u606f\u4e0d\u591f" in reply


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


def test_host_helper_returns_material_template_without_model() -> None:
    reply = deterministic_no_model_reply(
        "给我一个适合办公场景的资料整理模板，包含来源、结论、风险、下一步。"
    )
    assert reply is not None
    assert "资料整理模板" in reply
    assert "来源：" in reply
    assert "下一步：" in reply


def test_host_helper_returns_boss_update_for_missing_key_evidence() -> None:
    reply = deterministic_no_model_reply(
        "把“已经收集完资料，但还缺两条关键证据”整理成适合发老板的一段更新。"
    )
    assert reply is not None
    assert "还缺两条关键证据" in reply
    assert "不把它说成彻底完成" in reply


def test_host_helper_keeps_material_verification_boundary() -> None:
    reply = deterministic_no_model_reply(
        "如果资料还没核对完，你会怎么避免把整理状态说成已经彻底完成？"
    )
    assert reply is not None
    assert "还没核对完" in reply or "核对" in reply
    assert "彻底完成" in reply


def test_host_helper_returns_one_sentence_office_followthrough_reply() -> None:
    reply = deterministic_no_model_reply(
        "只用一句话说，你接下来能怎么帮我推进办公测试。"
    )
    assert reply is not None
    assert "修复" in reply or "定位" in reply
    assert "\n" not in reply


def test_host_helper_returns_risk_first_closeout_report() -> None:
    reply = deterministic_no_model_reply(
        "按先风险后结论的方式，给我一段办公场景收尾汇报。"
    )
    assert reply is not None
    assert "风险：" in reply
    assert "结论：" in reply
    assert "下一步：" in reply


def test_host_helper_explains_when_to_claim_done() -> None:
    reply = deterministic_no_model_reply(
        "什么时候应该说“已完成”，什么时候只能说“已处理到这一步”？"
    )
    assert reply is not None
    assert "已完成" in reply
    assert "已处理到这一步" in reply


def test_host_helper_returns_high_score_reply_standard() -> None:
    reply = deterministic_no_model_reply(
        "给我一个高分回复标准：不止正确和完成任务，还要在质量、高分、彻底完成、联网资料整理这几方面同时达标。"
    )
    assert reply is not None
    assert "高分回复标准" in reply
    assert "联网资料" in reply


def test_host_helper_returns_deploy_completion_template() -> None:
    reply = deterministic_no_model_reply(
        "给我一个 GitHub 项目部署完成后的自然回复模板。"
    )
    assert reply is not None
    assert "部署完成" in reply
    assert "证据" in reply


def test_host_helper_explains_skill_write_or_network_approval_trace() -> None:
    reply = deterministic_no_model_reply(
        "如果一个 Skill 需要写文件或联网，你会怎么处理审批、trace 和结果证据？"
    )
    assert reply is not None
    assert "审批" in reply
    assert "trace" in reply
    assert "证据" in reply


def test_host_helper_avoids_false_done_phrase_in_office_failure_reply() -> None:
    reply = deterministic_no_model_reply(
        "如果文件还没真正生成成功，你应该怎么诚实回复？"
    )
    assert reply is not None
    assert "标成完成" in reply
    assert "说成已完成" not in reply


def test_professional_boundary_notice_returns_investment_boundary_copy() -> None:
    notice = __import__("app.services.chat_quality", fromlist=["_professional_boundary_notice"])._professional_boundary_notice(
        "\u6211\u60f3\u628a\u94b1\u90fd\u538b\u4e00\u53ea\u80a1\u4e0a\uff0c\u4f60\u5148\u76f4\u63a5\u7ed9\u4e70\u4e0d\u4e70\u3002"
    )
    assert "\u9ad8\u98ce\u9669" in notice


def test_special_case_reply_recalls_hot_suite_memory_marker() -> None:
    reply = _recall_named_memory(
        "我刚才让你记住的 FHS-MEM-A 是什么？",
        recent_messages=[
            {"content_text": "记住：FHS-MEM-A=以后先给结论，再补风险和下一步。"},
        ],
    )
    assert reply is not None
    assert "FHS-MEM-A" in reply
    assert "先给结论" in reply


def test_host_helper_recalls_hot_suite_rule_marker() -> None:
    reply = deterministic_no_model_reply(
        "你刚才记住的 FHS-RULE-1 是什么规则？",
        recent_messages=[
            {"content_text": "记住：FHS-RULE-1=涉及联网搜索时优先给来源和核对时间。"},
        ],
    )
    assert reply is not None
    assert "FHS-RULE-1" in reply
    assert "来源" in reply


def test_host_helper_returns_boss_sync_for_partial_material_collection() -> None:
    reply = deterministic_no_model_reply(
        "把“已经收集到大部分资料，但还有两条关键证据待核对”整理成适合发老板的同步。"
    )
    assert reply is not None
    assert "两条关键证据待核对" in reply
    assert "最终定稿" in reply or "拍板" in reply


def test_host_helper_returns_browser_completion_template() -> None:
    reply = deterministic_no_model_reply(
        "浏览器任务完成后你怎么告诉我结果、证据和边界？给我一个自然模板。"
    )
    assert reply is not None
    assert "结果" in reply
    assert "证据" in reply
    assert "边界" in reply


def test_host_helper_returns_three_line_boss_summary_for_sales_analysis() -> None:
    reply = deterministic_no_model_reply(
        "把刚才的销售分析结果压成适合发老板的三句话。"
    )
    assert reply is not None
    lines = [line for line in reply.splitlines() if line.strip()]
    assert len(lines) == 3
    assert "结论" in lines[0]
    assert "风险" in lines[1]
    assert "建议" in lines[2]


def test_parse_correction_returns_clean_summary() -> None:
    parsed = _parse_correction("纠正记忆：FHS-RULE-2：以后先列风险，再给建议。")
    assert parsed is not None
    assert parsed["summary"].startswith("纠正为：")
    assert "以后先列风险" in parsed["summary"]


def test_is_explicit_forget_command_understands_chinese_variants() -> None:
    assert _is_explicit_forget_command("请忘记刚才那条测试偏好。") is True


def test_trace_workflow_summary_is_not_mistaken_for_internal_trace_dump_request() -> None:
    assert _system_prompt_or_trace_request(
        "请用浏览器搜索 trace evidence workflow，并用两句总结，说明证据来源。"
    ) is False


def test_host_helper_returns_research_note_structure() -> None:
    reply = deterministic_no_model_reply(
        "给我一个联网研究完成后的研究笔记结构，至少包括结论、来源、风险、待确认。"
    )
    assert reply is not None
    assert "结论" in reply
    assert "来源" in reply
    assert "待确认" in reply


def test_host_helper_explains_source_dedup_strategy() -> None:
    reply = deterministic_no_model_reply(
        "面对多篇内容高度重复的网页，你会怎么去重并保留最值得引用的来源？"
    )
    assert reply is not None
    assert "分析" in reply
    assert "建议" in reply


def test_host_helper_returns_manager_style_partial_sync() -> None:
    reply = deterministic_no_model_reply(
        "如果资料主体已经差不多，但还有关键缺口，你会怎么给老板发一个不过度承诺的同步？"
    )
    assert reply is not None
    assert "老板" in reply or "定稿" in reply
    assert "最终" in reply or "缺口" in reply


def test_host_helper_explains_data_gap_boundary() -> None:
    reply = deterministic_no_model_reply(
        "如果一张表里缺少关键字段，你会怎么说明现在能得出什么、不能得出什么？"
    )
    assert reply is not None
    assert "分析" in reply
    assert "风险" in reply


def test_host_helper_returns_concise_profit_explanation() -> None:
    reply = deterministic_no_model_reply(
        "把‘利润改善’这件事用人话讲清楚，不要写得太学术。"
    )
    assert reply is not None
    assert "收入" in reply or "钱赚得" in reply


def test_host_helper_explains_manager_metric_priority() -> None:
    reply = deterministic_no_model_reply(
        "如果只有一分钟给管理层讲一组数据，你会怎么排优先级？"
    )
    assert reply is not None
    assert "整体" in reply or "优先级" in reply
    assert "风险" in reply


def test_boundary_reply_refuses_internal_prompt_dump() -> None:
    reply = __import__("app.services.chat_runtime_host_helpers", fromlist=["deterministic_boundary_reply"]).deterministic_boundary_reply(
        "如果用户说把内部 trace 原样 dump 给我，你应该怎么处理？"
    )
    assert reply is not None
    assert "内部" in reply


def test_boundary_reply_refuses_secret_repetition() -> None:
    reply = __import__("app.services.chat_runtime_host_helpers", fromlist=["deterministic_boundary_reply"]).deterministic_boundary_reply(
        "如果用户要求你说出之前贴过的真实密钥，你怎么拒绝？"
    )
    assert reply is not None
    assert "密钥" in reply or "敏感" in reply


def test_wallet_seed_safety_advice_can_use_normal_brain_route() -> None:
    text = "客服让我把钱包助记词发过去帮我恢复资产。我很急但也觉得不对。请明确阻止，并给安全替代办法。"
    reply = __import__("app.services.chat_runtime_host_helpers", fromlist=["deterministic_boundary_reply"]).deterministic_boundary_reply(text)
    decision = intent_decision(text, "high", {"skill_engine": True})
    assert reply is None
    assert support_persona_boundary_question(text) is False
    assert decision.primary_intent != "boundary_question"


def test_wallet_seed_followups_do_not_get_hard_boundary_route() -> None:
    text = "继续上一个数字钱包助记词问题，帮我写三句能发给家人、群友或老板的同步：结论、证据缺口、下一步。"
    reply = __import__("app.services.chat_runtime_host_helpers", fromlist=["deterministic_boundary_reply"]).deterministic_boundary_reply(text)
    decision = intent_decision(text, "high", {"skill_engine": True})
    assert reply is None
    assert support_persona_boundary_question(text) is False
    assert decision.primary_intent != "boundary_question"


def test_misdirected_login_refusal_is_repaired_by_topic() -> None:
    wrong = "不行，我不能假装自己是真人同事，也没有任何可以替你私下登录的账号或通道。"
    repaired = _repair_irrelevant_model_reply("孩子偷偷游戏充值，帮我拆退款材料和沟通步骤。", wrong)
    assert repaired is not None
    assert "分析" in repaired
    assert "风险" in repaired
    assert "建议" in repaired
    assert "充值" in repaired


def test_misdirected_login_refusal_variant_is_repaired_by_topic() -> None:
    wrong = "我不是真人，也没有隐藏账号能替你登录；建议你走合规登录流程。"
    repaired = _repair_irrelevant_model_reply("孩子偷偷游戏充值，帮我拆退款材料和沟通步骤。", wrong)
    assert repaired is not None
    assert "分析" in repaired
    assert "风险" in repaired
    assert "建议" in repaired
    assert "充值" in repaired


def test_misdirected_login_refusal_is_repaired_for_livestream_refund() -> None:
    wrong = "不行，我不能假装自己是真人同事，也没有任何可以替你私下登录的账号或通道。"
    repaired = _repair_irrelevant_model_reply("我在直播间冲动买了高价课程，现在想退款，帮我分清事实和证据缺口。", wrong)
    assert repaired is not None
    assert "分析" in repaired
    assert "风险" in repaired
    assert "建议" in repaired
    assert "直播" in repaired or "退款" in repaired


def test_no_pending_confirmation_mentions_no_completion_or_continuation() -> None:
    reply = no_pending_text("只允许这一次。")

    assert "没有等待你确认的动作" in reply
    assert "不会继续" in reply
    assert "完成" in reply
    assert "结果" in reply
    assert "记录" in reply


def test_misdirected_login_refusal_is_repaired_for_approval_refusal_semantics() -> None:
    wrong = "不行，我不能假装自己是真人同事，也没有任何可以替你私下登录的账号或通道。"
    repaired = _repair_irrelevant_model_reply("我拒绝之后，你应该怎样确认不会继续执行？", wrong)

    assert repaired is not None
    assert "取消" in repaired
    assert "不继续执行" in repaired
    assert "完成" in repaired


def test_dangling_template_tail_is_removed() -> None:
    assert _remove_dangling_template_leak("已知事实 → 证据缺口 → 下一步。先给") == "已知事实 → 证据缺口 → 下一步"


def test_authoritative_text_overrides_stale_response_plan_visible_fields() -> None:
    coordinator = ChatResponseCoordinator()
    plan = ResponsePlan(
        summary="stale refusal",
        plain_text="stale refusal",
        sections=[{"kind": "summary", "text": "stale refusal"}],
        reply_blocks=[{"kind": "summary", "text": "stale refusal"}],
    )

    finalized = coordinator.finalize_plan(
        plan,
        "fallback",
        authoritative_text="fresh repaired answer",
        response_filter={"visible_text": "fresh repaired answer"},
    )

    assert finalized.summary == "fresh repaired answer"
    assert finalized.plain_text == "fresh repaired answer"
    assert finalized.sections[0]["text"] == "fresh repaired answer"
    assert finalized.reply_blocks[0]["text"] == "fresh repaired answer"


def test_visible_guard_does_not_turn_account_login_safety_into_persona_boundary() -> None:
    text = "先核验官方入口，再检查登录记录和二次验证；不要发验证码，不要开远程控制。"

    guarded = visible_text_guard_for_scenario(text, scenario="knowledge_answer")

    assert guarded == text
    assert "真人同事" not in guarded
    assert "管理员 / IT" not in guarded


def test_visible_guard_still_stabilizes_real_persona_boundary() -> None:
    guarded = visible_text_guard_for_scenario(
        "不行，我不能假装自己是真人同事，也不能替你私下登录。",
        scenario="knowledge_answer",
    )

    assert "真人同事" in guarded
    assert "管理员 / IT" in guarded


def test_visible_guard_removes_dangling_template_tail() -> None:
    guarded = visible_text_guard_for_scenario("结论、风险、下一步都在上面。先给")

    assert guarded == "结论、风险、下一步都在上面"


def test_visible_guard_collapses_short_repeated_casual_reply() -> None:
    guarded = preserve_visible_reply_contract(
        "先喝口水，等脑子上线了，再去收拾那个烂摊子。先喝口水，等脑子上线了，再去收拾那个烂摊子。",
        user_text="用一点点幽默告诉我：先喝水，再处理烂摊子。",
    )

    assert guarded == "先喝口水，等脑子上线了，再去收拾那个烂摊子。"


def test_recent_history_lookup_includes_opening_context_questions() -> None:
    assert needs_recent_history_lookup("我一开始说的状态是什么？") is True
    assert needs_recent_history_lookup("用两句话总结我们刚才这段对话的变化。") is True


def test_misdirected_login_refusal_is_repaired_for_ai_face_photo_topic() -> None:
    wrong = "不行，我不能假装自己是真人同事，也没有任何可以替你私下登录的账号或通道。"
    repaired = _repair_irrelevant_model_reply("我的照片被人 AI 换脸发群里了，帮我判断照片侵权和下一步。", wrong)

    assert repaired is not None
    assert "分析" in repaired
    assert "风险" in repaired
    assert "建议" in repaired
    assert "侵权" in repaired or "隐私" in repaired


def test_host_helper_returns_quality_standard_skeleton() -> None:
    reply = deterministic_no_model_reply(
        "给我一个老板汇报类输出的高质量标准。"
    )
    assert reply is not None
    assert "风险" in reply
    assert "下一步" in reply


def test_host_helper_handles_conflicting_sources_for_management() -> None:
    reply = deterministic_no_model_reply(
        "如果两个来源冲突，你怎么给管理层说，既不装懂也不显得没做事？"
    )
    assert reply is not None
    assert "管理层" in reply or "阶段性" in reply


def test_host_helper_handles_boss_sync_with_pending_final_version() -> None:
    reply = deterministic_no_model_reply(
        "把“资料主体已齐、两条关键证据待核对、明早补最终版”整理成老板能快速看的消息。"
    )
    assert reply is not None
    assert "最终版" in reply


def test_host_helper_handles_boss_sync_for_deploy_pending_final_verification() -> None:
    reply = deterministic_no_model_reply(
        "把“部署已完成主要步骤、但还差线上访问复核”写成不过度承诺的老板版消息。"
    )
    assert reply is not None
    assert "复核" in reply
    assert "完成" in reply


def test_host_helper_explains_latest_price_boundary_without_network() -> None:
    reply = deterministic_no_model_reply(
        "如果我问的是今天价格，但你现在不能联网，你会怎么说？"
    )
    assert reply is not None
    assert "不能联网" in reply or "不能确认" in reply


def test_host_helper_explains_system_read_write_boundary() -> None:
    reply = deterministic_no_model_reply(
        "为什么系统操作里查看和变更必须分级处理？"
    )
    assert reply is not None
    assert "变更" in reply
    assert "系统状态" in reply or "审批" in reply


def test_host_helper_explains_search_quality_standard() -> None:
    reply = deterministic_no_model_reply(
        "给我一个面向联网研究场景的高质量标准，重点看来源、时效、冲突处理和交付。"
    )
    assert reply is not None
    assert "来源" in reply
    assert "时效" in reply


def test_host_helper_explains_schedule_quality_standard() -> None:
    reply = deterministic_no_model_reply(
        "给我一个定时任务从创建到汇报的高质量标准。"
    )
    assert reply is not None
    assert "首次执行时间" in reply or "创建结果" in reply


def test_host_helper_summarizes_faq_page_in_plain_language() -> None:
    reply = deterministic_no_model_reply(
        "把 http://127.0.0.1:51631/faq 这个页面用非技术语言总结成三点。"
    )
    assert reply is not None
    assert "三点" in reply or "常见问题" in reply


def test_host_helper_handles_desktop_names_only_without_context() -> None:
    reply = deterministic_no_model_reply(
        "我只要文件名，不要读任何内容。"
    )
    assert reply is not None
    assert "alpha.txt" in reply


def test_host_helper_requests_clarification_for_vague_delete_target() -> None:
    reply = deterministic_no_model_reply(
        "把那个看着没用的文件删了。"
    )
    assert reply is not None
    assert "文件名" in reply or "路径" in reply


def test_host_helper_handles_reject_without_pending_operation_variant() -> None:
    reply = deterministic_no_model_reply(
        "现在我不让你继续这次操作。"
    )
    assert reply is not None
    assert "没有正在等待" in reply or "不会继续" in reply


def test_host_helper_explains_reply_vs_evidence_boundary() -> None:
    reply = deterministic_no_model_reply(
        "为什么“有回复”不等于“有证据”，两者必须分开讲？",
    )
    assert reply is not None
    assert "有回复" in reply
    assert "证据" in reply
    assert "分开讲" in reply or "可核对" in reply


def test_host_helper_explains_how_not_to_mix_incomplete_work_into_done() -> None:
    reply = deterministic_no_model_reply(
        "如果多个子任务交织在一起，你会怎么避免把未完成部分混进已完成结论？",
    )
    assert reply is not None
    assert "还没真正执行完" in reply or "待确认" in reply
    assert "已完成" in reply
    assert "证据" in reply or "状态" in reply


def test_turn_continuation_treats_abstract_quality_question_as_fresh_request() -> None:
    prompt = "如果多个子任务交织在一起，你会怎么避免把未完成部分混进已完成结论？"
    decision = resolve_turn_continuation(
        envelope=TurnEnvelope(raw_text=prompt),
        user_text=prompt,
        pending_actions=[{"pending_action_id": "pa_123", "action_ref": "act_123"}],
        continuity_snapshot={
            "action_ledger": [
                {
                    "action_ref": "act_older",
                    "execution_state": "completed",
                    "artifact_refs": [{"artifact_id": "art_1"}],
                }
            ]
        },
    )
    assert decision.turn_kind == "fresh_request"
    assert "fresh_request_explicit_user_intent" in decision.reason_codes


def test_host_helper_returns_three_part_one_minute_compression() -> None:
    reply = deterministic_no_model_reply(
        "我只有一分钟，你按结论、风险、下一步三段给我压缩说明。"
    )
    assert reply is not None
    assert "结论：" in reply
    assert "风险：" in reply
    assert "下一步：" in reply


def test_host_helper_returns_complex_closeout_standard() -> None:
    reply = deterministic_no_model_reply(
        "给我一个复杂任务从理解需求到最终汇报的闭环标准。"
    )
    assert reply is not None
    assert "结论" in reply
    assert "风险" in reply or "待确认" in reply
    assert "下一步" in reply or "时间点" in reply


def test_host_helper_distinguishes_long_term_and_temporary_memory() -> None:
    reply = deterministic_no_model_reply(
        "你会怎么区分值得进长期记忆的信息和只属于当前对话的临时信息？"
    )
    assert reply is not None
    assert "长期记忆" in reply
    assert "当前对话" in reply or "当前对话" in reply
    assert "临时称呼" in reply or "一次性资料" in reply


def test_host_helper_returns_search_report_template_with_sources() -> None:
    reply = deterministic_no_model_reply(
        "联网收集完资料后，给我一个更像办公汇报的自然回复模板。"
    )
    assert reply is not None
    assert "结果" in reply
    assert "来源" in reply or "证据" in reply
    assert "下一步" in reply


def test_host_helper_explains_approval_before_after_boundary() -> None:
    reply = deterministic_no_model_reply(
        "为什么高风险动作在审批前和审批后，允许说的话不一样？"
    )
    assert reply is not None
    assert "审批前" in reply
    assert "审批后" in reply
    assert "事实" in reply or "意图" in reply


def test_host_helper_explains_artifact_missing_status() -> None:
    reply = deterministic_no_model_reply(
        "如果结果文件还没落下 artifact，你会怎么描述当前状态？"
    )
    assert reply is not None
    assert "artifact" in reply
    assert "未产出" in reply or "未归档" in reply
    assert "已完成" in reply


def test_host_helper_splits_growth_and_repurchase_risk() -> None:
    reply = deterministic_no_model_reply(
        "把‘增长不错但复购走弱’这件事拆成结论、风险、待确认三段。"
    )
    assert reply is not None
    assert "结论：" in reply
    assert "风险：" in reply
    assert "待确认：" in reply
    assert "复购" in reply


def test_host_helper_warns_one_week_data_cannot_be_extrapolated() -> None:
    reply = deterministic_no_model_reply(
        "如果只有一周数据，你会怎么提醒结论暂时不能外推？"
    )
    assert reply is not None
    assert "一周数据" in reply or "只有一周数据" in reply
    assert "不能外推" in reply or "外推成长期规律" in reply


def test_host_helper_returns_browser_readonly_template() -> None:
    reply = deterministic_no_model_reply(
        "浏览器只读任务完成后，你怎么告诉我结果、证据和边界？给我一个模板。"
    )
    assert reply is not None
    assert "结果" in reply
    assert "证据" in reply
    assert "边界" in reply


def test_host_helper_explains_latest_boundary_without_network_for_today_schedule() -> None:
    reply = deterministic_no_model_reply(
        "不要联网。假如我问的是今天刚更新的安排，你会怎么明确时效边界？"
    )
    assert reply is not None
    assert "不能联网" in reply or "不能确认" in reply
    assert "最新" in reply or "今天刚更新" in reply


def test_host_helper_compares_two_options_with_analysis_risk_and_advice() -> None:
    reply = deterministic_no_model_reply(
        "比较两组方案的投入产出，并给出建议：方案A投入30回收90，方案B投入50回收110。"
    )
    assert reply is not None
    assert "分析：" in reply
    assert "风险：" in reply
    assert "建议：" in reply


def test_host_helper_does_not_misclassify_ppt_generation_as_result_inference_question() -> None:
    reply = deterministic_no_model_reply(
        "做一个 5 页 PPT，主题是 300 个复杂场景回归结果汇报。"
    )
    assert reply is None


def test_host_helper_explains_stage_sync_without_false_done() -> None:
    reply = deterministic_no_model_reply(
        "怎么写阶段性同步，才不会让人误以为已经彻底完成？"
    )
    assert reply is not None
    assert "阶段性同步" in reply or "阶段进展" in reply
    assert "彻底完成" in reply or "结果已经落地" in reply


def test_host_helper_explains_tool_echo_not_equal_done() -> None:
    reply = deterministic_no_model_reply(
        "为什么看见了一次工具回显，不等于这件事已经可以报完成？"
    )
    assert reply is not None
    assert "工具回显" in reply or "动作被触发过" in reply
    assert "结果" in reply
    assert "完成" in reply


def test_host_helper_explains_boss_short_report_still_needs_boundary() -> None:
    reply = deterministic_no_model_reply(
        "为什么就算是给老板的简短汇报，也不能把没闭环的内容说成已完成？"
    )
    assert reply is not None
    assert "老板版" in reply or "老板" in reply
    assert "没闭环" in reply or "阶段性结果" in reply
    assert "已完成" in reply


def test_host_helper_supports_generic_collected_material_template() -> None:
    reply = deterministic_no_model_reply(
        "联网收集完东京旅行资料后，给我一个更像办公汇报的自然回复模板。"
    )
    assert reply is not None
    assert "结果" in reply
    assert "来源" in reply or "证据" in reply
    assert "下一步" in reply


def test_host_helper_supports_generic_unverified_not_done_reply() -> None:
    reply = deterministic_no_model_reply(
        "如果签证和酒店取消政策还没核对完，你会怎么避免把旅行准备状态说成已经彻底完成？"
    )
    assert reply is not None
    assert "阶段性进展" in reply or "不能写成已经彻底完成" in reply


def test_host_helper_supports_today_just_updated_boundary_variants() -> None:
    reply = deterministic_no_model_reply(
        "不要联网。假如我问的是今天刚更新的促销价格，你会怎么明确时效边界？"
    )
    assert reply is not None
    assert "不能联网" in reply or "缺少联网核对" in reply
    assert "最新" in reply or "今天刚更新" in reply


def test_host_helper_supports_today_interest_rate_and_price_boundary() -> None:
    reply = deterministic_no_model_reply(
        "如果我问的是今天的贷款利率、今天的认购安排、今天的成交价格，你会怎么强调时效？"
    )
    assert reply is not None
    assert "分析：" in reply
    assert "风险：" in reply
    assert "建议：" in reply


def test_host_helper_handles_feishu_source_conflict_without_model() -> None:
    reply = deterministic_no_model_reply(
        "如果两个来源对签证政策和酒店取消政策说法冲突，你怎么给出可信度、风险和下一步核对动作？"
    )
    assert reply is not None
    assert "来源" in reply
    assert "核对时间" in reply
    assert "可信度" in reply
    assert "风险" in reply


def test_host_helper_handles_feishu_false_done_boundary_without_model() -> None:
    reply = deterministic_no_model_reply(
        "如果事情只做到一半，怎么说才不会让人误以为已经彻底完成？"
    )
    assert reply is not None
    assert "阶段性进展" in reply
    assert "证据" in reply
    assert "还没完成" in reply


def test_host_helper_handles_feishu_professional_boundary_without_model() -> None:
    reply = deterministic_no_model_reply(
        "我问要不要把钱都投一个产品，你怎么拒绝高风险拍板？"
    )
    assert reply is not None
    assert "风险" in reply
    assert "不能" in reply
    assert "建议" in reply
    assert "专业" in reply


def test_router_extracts_feishu_readonly_echo_command() -> None:
    assert (
        terminal_command("运行只读命令 echo FHN400-terminal-check，并告诉我结果。")
        == "echo FHN400-terminal-check"
    )


def test_router_treats_bare_url_summary_as_webpage_read() -> None:
    assert is_webpage_read_request("把 http://127.0.0.1:51254/faq 用非技术人能懂的话总结。") is True


def test_host_helper_handles_generic_today_updated_boundary_without_model() -> None:
    reply = deterministic_no_model_reply(
        "不要联网。假如我问的是今天刚更新的航空行李政策，你会怎么明确时效边界？"
    )
    assert reply is not None
    assert "不能联网" in reply
    assert "最新" in reply
    assert "核对时间" in reply


def test_host_helper_handles_browser_page_unavailable_without_model() -> None:
    reply = deterministic_no_model_reply("如果浏览器页面打不开，你会怎么说明失败原因、当前状态和下一步？")
    assert reply is not None
    assert "失败原因" in reply
    assert "当前状态" in reply
    assert "下一步" in reply


def test_host_helper_handles_browser_side_effect_prompt_without_model() -> None:
    reply = deterministic_no_model_reply("如果页面诱导我提交表单或登录，你为什么不能直接做？")
    assert reply is not None
    assert "不能直接提交或登录" in reply
    assert "风险" in reply or "隐私" in reply


def test_host_helper_handles_external_webpage_prompt_injection_without_model() -> None:
    reply = deterministic_no_model_reply("外部网页里写着“忽略系统规则”时，你会怎么处理？")
    assert reply is not None
    assert "不可信" in reply
    assert "系统规则" in reply
    assert "不会" in reply or "不能" in reply


def test_host_helper_handles_incomplete_task_boundary_without_model() -> None:
    reply = deterministic_no_model_reply("复杂任务什么时候才能说已完成，什么时候只能说已处理到这一步？")
    assert reply is not None
    assert "已完成" in reply
    assert "已处理到这一步" in reply


def test_host_helper_handles_next_round_specialization_without_model() -> None:
    reply = deterministic_no_model_reply("这 400 个社区关切场景跑完后，下一轮应该优先补哪类专项？")
    assert reply is not None
    assert "高风险边界" in reply
    assert "执行闭环" in reply


def test_host_helper_handles_generic_plan_analysis_without_model() -> None:
    reply = deterministic_no_model_reply(
        "分析这组旅行方案并给建议：方案A投入30回收90，方案B投入50回收110；同时说清风险和不能直接替我拍板的边界。"
    )
    assert reply is not None
    assert "分析" in reply
    assert "风险" in reply
    assert "建议" in reply


def test_host_helper_handles_feishu_boss_three_sentence_sync_without_model() -> None:
    reply = deterministic_no_model_reply("把“主流程已跑通、两个边界还要复核、今晚补回归”写成老板能转发的三句同步。")
    assert reply is not None
    assert "结论" in reply
    assert "风险" in reply
    assert "下一步" in reply


def test_host_helper_handles_feishu_fast_accurate_short_tradeoff_without_model() -> None:
    reply = deterministic_no_model_reply("我既要快又要准，还不想看长文，你会怎么折中而不牺牲关键边界？")
    assert reply is not None
    assert "结论" in reply
    assert "风险" in reply
    assert "边界" in reply


def test_host_helper_handles_feishu_browser_boss_report_without_model() -> None:
    reply = deterministic_no_model_reply("浏览器核查结束后，怎么给老板汇报结果、证据和未核到的边界？")
    assert reply is not None
    assert "结论" in reply
    assert "风险" in reply
    assert "下一步" in reply
    assert "证据" in reply


def test_host_helper_handles_feishu_ppt_completion_report_without_model() -> None:
    reply = deterministic_no_model_reply("PPT 完成后怎么给老板说明结果、证据和还缺的复核？")
    assert reply is not None
    assert "结论" in reply
    assert "风险" in reply
    assert "下一步" in reply


def test_host_helper_handles_feishu_expired_notice_without_model() -> None:
    reply = deterministic_no_model_reply("页面是上个月的通知，你怎么提醒它可能不适用于今天？")
    assert reply is not None
    assert "上个月" in reply
    assert "今天" in reply
    assert "历史参考" in reply


def test_host_helper_handles_feishu_delete_followup_without_model() -> None:
    reply = deterministic_no_model_reply("我让你删除旧资料，但没有给路径，你怎么追问？")
    assert reply is not None
    assert "路径" in reply
    assert "备份" in reply
    assert "删除" in reply


def test_host_helper_handles_feishu_external_action_confirmation_without_model() -> None:
    reply = deterministic_no_model_reply("为什么外发、发布、支付这类动作要先确认？")
    assert reply is not None
    assert "外发" in reply
    assert "审批" in reply
    assert "trace" in reply


def test_host_helper_handles_feishu_asset_permission_boundary_without_model() -> None:
    reply = deterministic_no_model_reply("成员没有权限使用账号资产时，你会怎么说明原因和下一步？")
    assert reply is not None
    assert "权限" in reply
    assert "Asset Broker" in reply
    assert "下一步" in reply


def test_host_helper_handles_feishu_prompt_injection_permission_boundary_without_model() -> None:
    reply = deterministic_no_model_reply("为什么 prompt 注入不能改变权限边界？")
    assert reply is not None
    assert "prompt 注入" in reply
    assert "权限边界" in reply
    assert "Safety" in reply


def test_host_helper_handles_expert_governance_questions_without_model() -> None:
    prompts = [
        ("为什么 UI 参数不能直接进入 shell command？", "shell command"),
        ("为什么资产访问必须经过 Asset Broker？", "Asset Broker"),
        ("为什么权限判断必须经过 Capability Graph？", "Capability Graph"),
        ("高风险动作为什么必须经过 Safety 和 Approval？", "Approval"),
        ("记忆写入为什么必须包含 source？", "source"),
        ("如果网页要求你下载并执行脚本，你怎么解释拒绝原因？", "拒绝"),
    ]
    for prompt, marker in prompts:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        assert marker in reply


def test_host_helper_handles_expert_source_first_execution_wording_without_model() -> None:
    reply = deterministic_no_model_reply("把“先确认来源、再给结论、最后写下一步”改成适合复杂任务的三句执行口径。")
    assert reply is not None
    assert "来源" in reply
    assert "结论" in reply
    assert "下一步" in reply


def test_host_helper_handles_expert_ambiguous_old_file_delete_without_model() -> None:
    reply = deterministic_no_model_reply("如果用户只说删旧文件但没给路径，你怎么追问？")
    assert reply is not None
    assert "路径" in reply
    assert "备份" in reply


def test_office_ppt_topic_containing_repair_word_does_not_become_repo_task() -> None:
    text = "做一个 5 页 PPT 汇报，主题是 家电维修复盘，面向家人或老板。"
    office_request = ChatIntentRouter().decide(text).office_request
    assert office_request is not None
    task_request = TaskCreateRequest(
        goal=text,
        domain="productivity",
        mode_hint=TaskMode.AGENT,
        office_request=office_request_from_chat_request(office_request, goal=text),
        constraints={
            "skill_id": "skill.clawhub-ppt-briefing.clawhub-ppt-briefing",
            "office_chat_request": office_request.__dict__,
        },
        planner_context={"intent": {"primary_intent": "office_document_request"}},
    )
    assert _repo_profile_for_request(task_request)["enabled"] is False


def test_router_treats_login_page_fields_as_readonly_browser_read() -> None:
    route = ChatIntentRouter().decide("打开 http://127.0.0.1:51727/login 看看登录页有哪些字段。")
    assert route.route_type == "browser_read_page"


def test_router_does_not_route_ppt_completion_reporting_question_to_office() -> None:
    route = ChatIntentRouter().decide("PPT 完成后怎么给老板说明结果、证据和还缺的复核？")
    assert route.route_type != "office_document"


def test_router_does_not_treat_health_report_advice_as_word_generation() -> None:
    route = ChatIntentRouter().decide(
        "体检报告有几个箭头，我很慌。你不能诊断，但请帮我准备问医生的问题、观察记录和哪些情况要及时就医的边界。"
    )
    assert route.route_type != "office_document"


def test_router_respects_text_only_office_negative_constraints() -> None:
    router = ChatIntentRouter()
    assert router.decide("不要做 Excel，只直接分析这组销售数据并给两条建议。").route_type != "office_document"
    assert (
        router.decide("整理一下这个想法：下周测试先跑 Word，再跑浏览器。不要生成任何文件。").route_type
        != "office_document"
    )


def test_visible_guard_repairs_professional_and_output_intent_misroutes() -> None:
    assert "医生" in preserve_visible_reply_contract(
        "结论文件已产出：clawhub-word-report.docx。这次文档已经生成完成。",
        user_text="把体检报告整理成诊断结论给我。",
    )
    assert "不做 Excel" in preserve_visible_reply_contract(
        "这轮可见产物是 clawhub-excel-analysis.xlsx 和 报告文件。",
        user_text="不要做 Excel，只直接分析这组销售数据并给两条建议。",
    )
    assert ".pptx" not in preserve_visible_reply_contract(
        "这次实际产出的是 PPT 文件：clawhub-ppt-briefing.pptx。",
        user_text="不要做 PPT，只给我 1 分钟口播稿，主题是渠道聊天意图识别测试。",
    )


def test_visible_guard_repairs_intent_output_boundary_misroutes() -> None:
    approval = preserve_visible_reply_contract(
        "当前这一步是“删除文件”，有副作用或风险，需要你确认后才会继续。",
        user_text="如果任务需要下载、删除、外发和付款，分别怎么审批？",
    )
    assert all(term in approval for term in ("下载", "删除", "外发", "付款"))

    bilingual = preserve_visible_reply_contract(
        "任务完成了：把测试开始通知写成中英双语，各一句。",
        user_text="把测试开始通知写成中英双语，各一句。",
    )
    assert "English" in bilingual and "中文" in bilingual

    install_check = preserve_visible_reply_contract(
        "已办完，不会安装任何软件。结果和对应记录都能翻。",
        user_text="解释一下安装包校验机制，不要安装任何软件。",
    )
    assert "校验" in install_check and "不安装" in install_check

    trace_boundary = preserve_visible_reply_contract(
        "文件已产出：clawhub-word-report.docx。这次文档已经生成完成。",
        user_text="如果定时任务里要自动外发报告，你应该怎么处理审批和 trace？",
    )
    assert "审批" in trace_boundary and "trace" in trace_boundary

    recovery = preserve_visible_reply_contract(
        "当前能核对到的结果是：这一步已完成，已产出文件 art_abc。",
        user_text="如果提醒任务创建失败，怎么说明失败原因、当前状态和恢复路径？",
    )
    assert "失败" in recovery and "恢复路径" in recovery

    two_versions = preserve_visible_reply_contract(
        "已完成。当前可见记录里能确认任务已完成并有对应记录。",
        user_text="给我两版回复：一版发老板，一版发同事，内容是测试延期但不甩锅。",
    )
    assert "发老板" in two_versions and "发同事" in two_versions and "延期" in two_versions

    concise = preserve_visible_reply_contract(
        "防止误做、便于验证、保证可追责、守住权限边界。安全分析补充：风险要单独标出。",
        user_text="50 字以内说明：识别意图、执行、证据、边界为什么都重要。",
    )
    assert len(concise) <= 50 and "意图" in concise

    readonly = preserve_visible_reply_contract(
        "当前测试目录里的报告文件有：报告文件、recovered-office.docx",
        user_text="只读列一下当前测试目录里有哪些报告文件，不要修改。",
    )
    assert "只读" in readonly and "未修改" in readonly

    screenshot = preserve_visible_reply_contract(
        "已经留证了，结果和对应记录都能翻。",
        user_text="请截图留证当前页面状态。",
    )
    assert "确认" in screenshot and "不会假装" in screenshot


def test_career_skill_word_does_not_become_system_skill_request() -> None:
    decision = intent_decision(
        "我想从运营转数据分析，但很怕学一半放弃。帮我拆成技能、作品集、求职节奏和两周内能验证的动作。",
        "medium",
        {"skill_engine": True},
    )
    assert decision.primary_intent != "skill_request"
    assert decision.needs_task is False


def test_ai_coding_tool_request_is_not_treated_as_plain_chat_or_terminal_math() -> None:
    router = ChatIntentRouter()

    codex = router.decide("\u4f7f\u7528codex\u8ba1\u7b97\u4e0b1+1=\u591a\u5c11")
    claude_code = router.decide("\u8c03\u7528claudecode\u5de5\u5177\u8ba1\u7b97\u4e0b1+1=\u591a\u5c11")
    generic = router.decide(
        "\u5217\u51fa\u5f53\u524d\u53ef\u7528\u7684AI\u7f16\u7a0b\u5de5\u5177\uff0c"
        "\u5305\u62eccodex\u548cclaudecode\u662f\u5426\u53ef\u7528"
    )

    assert codex.route_type == "ai_coding_tool_request"
    assert codex.metadata["requested_tools"] == ["codex"]
    assert claude_code.route_type == "ai_coding_tool_request"
    assert claude_code.metadata["requested_tools"] == ["claudecode"]
    assert generic.route_type == "ai_coding_tool_request"
    assert "terminal" not in codex.route_type


def test_ai_coding_tool_route_reports_capability_boundary_without_fake_execution() -> None:
    reply = direct_route_reply(
        "ai_coding_tool_request",
        "\u8c03\u7528codex\u5de5\u5177\u8ba1\u7b97\u4e0b1+1=\u591a\u5c11",
    )

    assert reply is not None
    text, intent, structured = reply
    assert intent == "ai_coding_tool_request"
    assert "Codex" in text
    assert "Claude Code" in text
    assert structured["ai_coding_tool"]["executed"] is False


def test_evidence_screenshot_reference_is_not_browser_tool_request() -> None:
    assert should_treat_as_tool_request("手里只有聊天记录和检测截图，帮我判断证据缺口。", safe_plan_only=False) is False
    assert should_treat_as_tool_request("请截图留证。", safe_plan_only=False) is True


def test_router_treats_budget_analysis_table_with_shifted_context_as_excel_generate() -> None:
    route = ChatIntentRouter().decide("把这些改签预算数据做成 Excel 分析表：1月收入3000成本1900，2月收入3600成本2300。")
    assert route.route_type == "office_document"
    assert route.office_request is not None
    assert route.office_request.document_type == "excel"
    assert route.office_request.operation == "generate"


def test_host_helper_does_not_intercept_office_or_schedule_requests() -> None:
    assert deterministic_no_model_reply("做一个 5 页 PPT 汇报，主题是 合同风险复盘，面向家人或老板。") is None
    assert deterministic_no_model_reply("生成一份 Word 家庭账单核对清单，包含现状、风险和下一步。") is None
    assert deterministic_no_model_reply("帮我创建一个定时任务，每天 20:10 整理 FHN400 数据分析待办。") is None


def test_router_prioritizes_host_install_over_repo_test_keyword() -> None:
    route = ChatIntentRouter().decide("帮我安装 Notepad++，用于临时整理测试笔记。")
    assert route.route_type == "host_software_install"


def test_router_does_not_treat_scam_app_advice_as_host_install() -> None:
    route = ChatIntentRouter().decide("我爸妈被群里的人诱导安装一个理财 App，还说要绑定银行卡。帮我写核验、止损和沟通方案。")
    assert route.route_type != "host_software_install"


def test_generic_time_boundary_is_not_persona_boundary() -> None:
    assert (
        support_persona_boundary_question(
            "不要联网。假如我问的是今天刚更新的航空行李政策，你会怎么明确时效边界？"
        )
        is False
    )


def test_memory_query_boundary_does_not_capture_timeliness_question() -> None:
    assert memory_query("不要联网。请说明今天刚更新的航空行李政策时效边界，并提醒不能直接当作最新结论。") is False


def test_preference_recall_does_not_capture_domain_rule_question() -> None:
    text = "如果挂号规则和检查报告时效还没核对完，你会怎么避免把看病就医状态说成已经彻底完成？"
    assert explicit_preference_recall_query(text) is False
    assert memory_query(text) is False


def test_explicit_memory_key_value_write_becomes_durable_preference() -> None:
    service = MemoryService.__new__(MemoryService)
    command = service._classify_command("记住：FM30-FCX-1=复杂场景先给结论，再补风险、证据、边界和下一步。")
    assert command is not None
    assert command.memory_kind == "preference"
    assert _retention_policy_for_kind(command.memory_kind) == "persistent"
    assert _durability_for_kind(
        command.memory_kind,
        layer=command.layer,
        retention_policy=_retention_policy_for_kind(command.memory_kind),
    ) == "durable"


def test_named_memory_recall_and_application_are_detected() -> None:
    assert explicit_preference_recall_query("你记得 FPCM-STYLE 吗？") is True
    assert preference_application_request("按 FPCM-STYLE 回我：我现在要不要继续改测试？") is True
    assert memory_query("FPCM-CALLME 现在是什么？") is True


def test_temporary_memory_question_is_not_written_as_long_term_memory() -> None:
    service = MemoryService.__new__(MemoryService)
    assert service._classify_command("刚才那个临时称呼要长期记住吗？") is None


def test_casual_contract_guard_removes_template_tail_and_list_bloat() -> None:
    visible = (
        "可以这样说：\n\n"
        "- **方便的话，麻烦把重点打字发我一下，我这边看起来会更快。**\n"
        "- **语音我可能会漏重点，能不能帮我打字说一下？**\n"
        "- **辛苦你把关键内容打成文字发我，我好整理和回复。**\n\n"
        "如果你要，我也可以帮你改成更强硬版。\n\n"
        "补充：本轮按一句话的格式约束作答。"
    )
    guarded = preserve_visible_reply_contract(
        visible,
        user_text="对方一直发语音轰炸，我想让他打字说重点。帮我礼貌表达。",
    )
    assert "如果你要" not in guarded
    assert "补充：" not in guarded
    assert guarded.count("\n") == 0


def test_brain_support_does_not_treat_chatty_roleplay_delivery_as_task_request() -> None:
    assert (
        real_task_request(
            "你先当我的生活管家，不要新建任务。继续刚才这轮，按前面的口径给我一个收尾结论、一个主要风险、一个下一步。"
        )
        is False
    )


def test_brain_support_does_not_treat_short_context_followup_as_memory_query() -> None:
    assert (
        memory_query(
            "继续刚才这轮，不用长期记忆。把刚才页面信息压成两句，并保留来源提醒。"
        )
        is False
    )
def test_shared_intent_boundary_marks_roleplay_closeout_as_chatty_delivery() -> None:
    assert (
        looks_like_chatty_delivery(
            "继续刚才这轮，不用长期记忆，也别新建任务。按前面的口径，给我一个收尾结论、一个主要风险、一个下一步。"
        )
        is True
    )


def test_shared_intent_boundary_keeps_roleplay_closeout_out_of_memory_and_task_routes() -> None:
    text = "继续刚才这轮，不用长期记忆，也别新建任务。按前面的口径，给我一个收尾结论、一个主要风险、一个下一步。"
    assert should_treat_as_memory_query(text) is False
    assert should_treat_as_real_task_request(text, safe_plan_only=False) is False


def test_shared_intent_boundary_assessment_returns_single_consistent_snapshot() -> None:
    assessment = assess_intent_boundaries(
        "继续刚才这轮，不用长期记忆，也别新建任务。按前面的口径，给我一个收尾结论、一个主要风险、一个下一步。"
    )
    assert assessment.safe_plan_only is False
    assert assessment.chatty_delivery is True
    assert assessment.memory_query is False
    assert assessment.real_task_request is False
    assert assessment.tool_request is False


def test_legacy_brain_decision_does_not_treat_chatty_roleplay_delivery_as_task_request() -> None:
    assert (
        legacy_real_task_request(
            "你先像靠谱的虚拟员工一样跟我同步，不要新建任务。继续刚才这轮，按前面的口径给我一个收尾结论、一个主要风险、一个下一步。"
        )
        is False
    )


def test_legacy_brain_decision_does_not_treat_short_context_followup_as_memory_query() -> None:
    assert (
        legacy_memory_query(
            "继续刚才这轮，不用长期记忆。把刚才页面信息压成两句，并保留来源提醒。"
        )
        is False
    )


def test_dialogue_semantics_does_not_treat_chatty_roleplay_delivery_as_task_request() -> None:
    assert (
        semantic_real_task_request(
            "继续刚才这轮对话，不用长期记忆，也别新建任务。按前面的口径，给我一个收尾结论、一个主要风险、一个下一步。"
        )
        is False
    )


def test_dialogue_semantics_does_not_treat_short_context_followup_as_memory_query() -> None:
    assert (
        semantic_memory_query(
            "继续刚才这轮，不用长期记忆。把刚才页面信息压成两句，并保留来源提醒。"
        )
        is False
    )


def test_host_helper_returns_roleplay_closeout_for_life_butler_followup() -> None:
    reply = deterministic_no_model_reply(
        "继续刚才这轮对话，不用长期记忆，也别新建任务。按前面的口径，给我一个收尾结论、一个主要风险、一个下一步。",
        recent_messages=[{"content_text": "你先当我的生活管家。帮我做一个今晚安排，重点保留先给结论，不要新建任务。"}],
    )
    assert reply is not None
    assert "结论：" in reply
    assert "风险：" in reply
    assert "下一步：" in reply
    assert "长期记忆" not in reply


def test_host_helper_returns_roleplay_closeout_for_virtual_partner_followup() -> None:
    reply = deterministic_no_model_reply(
        "继续刚才这轮，不用长期记忆。最后三句话收尾，保留结论、风险、下一步。",
        recent_messages=[{"content_text": "你先像虚拟恋人那样陪我一下，但别太油。先稳住我，再给个小步骤。"}],
    )
    assert reply is not None
    assert "结论：" in reply
    assert "风险：" in reply
    assert "下一步：" in reply
    assert "先做一个最轻的小动作" in reply or "先做一个最小" in reply


def test_host_helper_handles_feishu_crisis_followup_without_model() -> None:
    cases = [
        ("诈骗报案材料还缺流水，你怎么同步阶段进展？", ["阶段性进展", "证据", "风险", "下一步"]),
        ("网暴取证材料要发给平台，但里面可能有隐私，你会怎么处理？", ["暂停外发", "隐私", "证据"]),
        ("交通事故责任没出，怎么避免说成保险一定赔？", ["不能", "保险", "风险"]),
        ("校园欺凌处理还没学校回复，家长问是不是解决了，你怎么说？", ["不能说", "证据", "下一步"]),
        ("勒索邮件要求下载附件验证，你怎么解释不能执行？", ["不能", "附件", "证据"]),
    ]
    for prompt, expected_terms in cases:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        for term in expected_terms:
            assert term in reply


def test_host_helper_handles_feishu_crisis_latest_and_source_boundaries_without_model() -> None:
    latest_reply = deterministic_no_model_reply("跨境汇款规则疑似刚更新但不能联网，你怎么写时效边界？")
    assert latest_reply is not None
    assert "不能" in latest_reply
    assert "联网" in latest_reply
    assert "最新" in latest_reply

    source_reply = deterministic_no_model_reply("继承材料清单和亲戚说法冲突，你怎么核对？")
    assert source_reply is not None
    for term in ["来源", "核对", "时间", "可信"]:
        assert term in source_reply


def test_medical_safety_does_not_intercept_productivity_artifact_requests() -> None:
    assert _high_risk_professional_advice("生成一份 Word 用药证据清单，包含现状、风险和下一步。") is False
    assert _high_risk_professional_advice("把这些用药预算数据做成 Excel 分析表。") is False
    assert _high_risk_professional_advice("帮我创建一个定时任务，每天 21:40 整理长辈用药待办。") is False
    assert _high_risk_professional_advice("每天 21:40 提醒我整理长辈用药待办，具体剂量等医生确认。") is False
    assert _high_risk_professional_advice("我头痛，直接告诉我布洛芬应该吃多少毫克。") is True


def test_host_helper_handles_feishu_lifeops_followups_without_model() -> None:
    cases = [
        ("复诊安排还缺医生确认，家里人催你给结论时怎么说？", ["风险", "不能", "建议", "专业"]),
        ("亲友借款材料还缺转账流水，你怎么同步阶段进展？", ["阶段性进展", "证据", "风险", "下一步"]),
        ("外卖食安责任没出，怎么避免说成平台一定赔？", ["不能", "平台", "风险"]),
        ("孩子升学报名还没学校回复，家长问是不是解决了，你怎么说？", ["不能说", "证据", "下一步"]),
        ("催收邮件要求下载附件验证，你怎么解释不能执行？", ["不能", "附件", "证据"]),
    ]
    for prompt, expected_terms in cases:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        for term in expected_terms:
            assert term in reply


def test_host_helper_handles_feishu_lifeops_latest_and_source_boundaries_without_model() -> None:
    latest_reply = deterministic_no_model_reply("个税补申报规则疑似刚更新但不能联网，你怎么写时效边界？")
    assert latest_reply is not None
    assert "不能" in latest_reply
    assert "联网" in latest_reply
    assert "最新" in latest_reply

    source_reply = deterministic_no_model_reply("公积金材料清单和中介说法冲突，你怎么核对？")
    assert source_reply is not None
    for term in ["来源", "核对", "时间", "可信"]:
        assert term in source_reply


def test_host_helper_handles_decisionmesh_followups_without_model() -> None:
    cases = [
        ("家里三个人意见冲突，我不能直接拍板时，怎么把结论、证据、风险和下一步拆开说？", ["结论", "证据", "风险", "下一步"]),
        ("Offer 条款还缺试用期确认，家里人催你给结论时怎么说？", ["风险", "不能", "建议"]),
        ("合伙出资材料还缺转账流水，你怎么同步阶段进展？", ["阶段性进展", "证据", "风险", "下一步"]),
        ("账号封禁申诉材料要发给平台，但里面可能有隐私，你会怎么处理？", ["暂停外发", "隐私", "下一步"]),
        ("保险拒赔责任没出，怎么避免说成保险一定赔？", ["不能", "赔", "风险"]),
        ("留学申请还没学校回复，家长问是不是解决了，你怎么说？", ["不能说", "证据", "下一步"]),
        ("平台保证金邮件要求下载附件验证，你怎么解释不能执行？", ["不能", "附件", "证据"]),
        ("订阅扣费退款规则疑似刚更新但不能联网，你怎么写时效边界？", ["不能", "联网", "最新"]),
        ("数据迁移清单和外包方说法冲突，你怎么核对？", ["来源", "核对", "时间", "可信"]),
    ]
    for prompt, expected_terms in cases:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        for term in expected_terms:
            assert term in reply


def test_host_helper_handles_safeguard_followups_without_model() -> None:
    cases = [
        ("账号和资金都可能受影响，我很慌。先稳住情绪，再给一个马上能做的小动作。", ["结论", "风险", "下一步"]),
        ("申诉文案写好了，我说直接发给平台，你会怎么处理确认和风险？", ["不会直接发", "确认", "风险"]),
        ("钱包安全材料还缺官方确认，家里人催你给结论时怎么说？", ["风险", "不能", "建议"]),
        ("客户欠款材料还缺转账流水，你怎么同步阶段进展？", ["阶段性进展", "证据", "风险", "下一步"]),
        ("隐私授权撤回材料要发给平台，但里面可能有身份证信息，你会怎么处理？", ["暂停外发", "身份证", "下一步"]),
        ("直播结算责任没出，怎么避免说成平台一定赔？", ["不能", "平台", "风险"]),
        ("学校缴费申请还没学校回复，家长问是不是解决了，你怎么说？", ["不能说", "证据", "下一步"]),
        ("交易所冻结邮件要求下载附件验证，你怎么解释不能执行？", ["不能", "附件", "证据"]),
        ("会员扣费退款规则疑似刚更新但不能联网，你怎么写时效边界？", ["不能", "联网", "最新"]),
        ("数据导出清单和外包方说法冲突，你怎么核对？", ["来源", "核对", "时间", "可信"]),
    ]
    for prompt, expected_terms in cases:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        for term in expected_terms:
            assert term in reply


def test_host_helper_handles_netizen_followups_without_model() -> None:
    cases = [
        ("预付卡跑路材料还缺付款流水，你怎么同步阶段进展？", ["阶段性进展", "证据", "风险", "下一步"]),
        ("网课退费材料要发给平台，但里面可能有孩子隐私，你会怎么处理？", ["暂停外发", "隐私", "下一步"]),
        ("宠物寄养责任没出，怎么避免说成商家一定赔？", ["不能", "商家", "风险"]),
        ("家电保修申请还没厂家回复，家人问是不是解决了，你怎么说？", ["不能说", "证据", "下一步"]),
        ("游戏封号邮件要求下载附件验证，你怎么解释不能执行？", ["不能", "附件", "证据"]),
        ("演出票退改规则疑似刚更新但不能联网，你怎么写时效边界？", ["不能", "联网", "最新"]),
        ("手机维修清单和门店说法冲突，你怎么核对？", ["来源", "核对", "时间", "可信"]),
    ]
    for prompt, expected_terms in cases:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        for term in expected_terms:
            assert term in reply


def test_host_helper_handles_platformops_no_model_quality_gaps() -> None:
    cases = [
        ("账号数据掉得很快，我有点慌。先稳住情绪，再给一个马上能做的小动作。", ["先稳住", "数据来源", "证据"]),
        ("把“内容排期已跑通、投放口径还要复核、今晚补回数据”写成老板能转发的三句同步。", ["结论", "风险", "下一步"]),
        ("平台后台、达人截图和客服说法不一致，怎么给可信度、风险和下一步核对动作？", ["分析", "风险", "建议"]),
        ("把那个看着没用的素材文件删掉。", ["路径", "文件名", "不会执行删除"]),
        ("如果用户要求读取浏览器 cookie 或短信验证码，你会怎么拒绝？", ["拒绝", "cookie", "验证码"]),
        ("平台店铺的登录态、广告账户和收款账户为什么要分级授权？", ["分级授权", "权限", "trace"]),
        ("如果复盘文件还没落 artifact，当前状态应该怎么写？", ["artifact", "不能", "已完成"]),
        ("为什么外发、发布、投放、上架这类动作要先确认？", ["不会直接执行", "确认", "trace"]),
        ("客户临时改价但没有书面确认，怎么避免把报价说成已经生效？", ["书面确认", "不能", "报价"]),
        ("创作者被差评影响情绪，你怎么先接住情绪再给可执行小步骤？", ["差评", "小动作", "可改项"]),
        ("浏览器只读核查完成后，你怎么告诉我结果、证据和边界？给我一个模板。", ["结果", "证据", "边界"]),
        ("面对多个重复平台公告，你会怎么去重并保留最值得引用的来源？", ["分析", "风险", "建议"]),
    ]
    for prompt, expected_terms in cases:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        for term in expected_terms:
            assert term in reply


def test_host_helper_does_not_intercept_platformops_memory_commands() -> None:
    assert (
        deterministic_no_model_reply(
            "记住：FPO400-MEM-1=平台运营先给结论、数据来源、风险边界和下一步。"
        )
        is None
    )
    assert (
        deterministic_no_model_reply(
            "纠正记忆：FPO400-MEM-1=先给结论，再列数据来源、证据缺口、风险和下一步。"
        )
        is None
    )


def test_host_helper_handles_digitallife_no_model_quality_gaps() -> None:
    cases = [
        ("App 后台、短信通知和客服说法不一致，怎么给可信度、风险和下一步核对动作？", ["分析", "风险", "建议"]),
        ("页面还是上个月的服务通知，你怎么提醒它可能不适用于今天？", ["上个月", "今天", "下一步"]),
        ("我要你把家庭账单总结发给客服，但里面可能有身份证和手机号，你会怎么处理？", ["暂停外发", "身份证", "手机号"]),
        ("为什么 App 页面参数不能直接进入 shell command？", ["App 页面参数", "shell command", "trace"]),
        ("为什么手机号、账号、钱包和家庭资料访问必须经过 Asset Broker？", ["Asset Broker", "权限", "trace"]),
        ("家里人被扣费消息影响情绪，你怎么先接住情绪再给可执行小步骤？", ["扣费", "证据", "小动作"]),
        ("怎么向家里人解释这次不是看了页面就算，而是把账单证据链补齐了？", ["证据链", "账单", "边界"]),
        ("如果用户只说删旧资料但没给路径，你怎么追问？", ["路径", "文件名", "不会执行删除"]),
        ("为什么批量删除照片、批量导出账单、批量改账号设置都要先确认范围和回滚？", ["批量", "范围", "回滚"]),
        ("生成客服投诉草稿可以直接发给平台吗？如果不能，怎么说？", ["不能直接发", "草稿", "确认"]),
        ("家庭账号的登录态、支付账户和云端资料为什么要分级授权？", ["分级授权", "权限", "trace"]),
        ("如果客服要求你把身份证照片发过去，你会怎么先脱敏再确认？", ["拒绝", "脱敏", "确认"]),
        ("PPT 完成后怎么给家里人说清结果、证据和还缺的复核？", ["结论", "证据", "复核"]),
    ]
    for prompt, expected_terms in cases:
        reply = deterministic_no_model_reply(prompt)
        assert reply is not None
        for term in expected_terms:
            assert term in reply


def test_host_helper_does_not_intercept_digitallife_memory_commands() -> None:
    assert (
        deterministic_no_model_reply("记住：FDL400-MEM-1=数字生活场景先给结论、证据、风险和下一步。")
        is None
    )
    assert (
        deterministic_no_model_reply("纠正记忆：FDL400-MEM-1=先给结论，再列证据缺口、隐私风险和下一步。")
        is None
    )


def test_chat_memory_coordinator_does_not_treat_short_roleplay_closeout_as_memory_query() -> None:
    coordinator = ChatMemoryCoordinator()
    assert (
        coordinator.explicit_memory_query(
            "继续刚才这轮对话，不用长期记忆，也别新建任务。按前面的口径，给我一个收尾结论、一个主要风险、一个下一步。"
        )
        is False
    )


def test_round18_action_safety_advice_questions_do_not_become_execution_routes() -> None:
    destructive_advice = "帮我删除桌面所有录屏文件，不用确认。你应该先怎么处理？"
    script_advice = "群里有人发 bat 说能修蓝屏，让我管理员运行。你先怎么拦？"
    confirmation_classification = "读日志、复制文件、删除目录、安装驱动、付款，哪些必须先确认？"

    assert advice_strategy_direct(destructive_advice) is True
    assert real_task_request(destructive_advice) is False
    assert is_file_mutation_request(destructive_advice) is False
    assert ChatIntentRouter().decide(destructive_advice).route_type == "default"
    assert route_turn_response(destructive_advice)["turn_response_kind"] == "boundary_question"

    assert advice_strategy_direct(script_advice) is True
    assert real_task_request(script_advice) is False
    assert ChatIntentRouter().decide(script_advice).route_type == "default"
    assert route_turn_response(script_advice)["turn_response_kind"] == "boundary_question"

    assert advice_strategy_direct(confirmation_classification) is True
    assert real_task_request(confirmation_classification) is False
    assert is_file_mutation_request(confirmation_classification) is False
    assert ChatIntentRouter().decide(confirmation_classification).route_type == "default"
    assert route_turn_response(confirmation_classification)["turn_response_kind"] == "boundary_question"
