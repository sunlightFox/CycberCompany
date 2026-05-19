from app.services.chat_runtime_host_helpers import terminal_command_reply
from app.services.chat_turn_execution import _scheduled_task_created_reply
from app.services.natural_chat import (
    _extract_temporary_nickname_command,
    _recall_named_memory,
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
