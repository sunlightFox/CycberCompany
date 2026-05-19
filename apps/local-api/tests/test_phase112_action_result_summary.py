from app.services.action_result_summary import clean_result_summary
from app.services.action_result_summary import summarize_completed_action_result


def test_phase112_completed_result_prefers_artifact_names() -> None:
    summary = summarize_completed_action_result(
        label="生成季度汇报",
        target="Q2 增长复盘",
        artifact_refs=[
            {"artifact_uri": "artifacts/clawhub-ppt-briefing.pptx"},
            {"name": "notes.docx"},
        ],
        result_summary="文档已经生成完成。",
    )

    assert summary == "已产出文件 clawhub-ppt-briefing.pptx、notes.docx"


def test_phase112_completed_result_filters_progress_style_summary() -> None:
    summary = summarize_completed_action_result(
        label="卸载 QQ",
        target="QQ",
        artifact_refs=[],
        result_summary="卸载 QQ这一步已经开始推进了，我会按实际结果继续汇报。",
    )

    assert summary == "卸载 QQ"
    assert clean_result_summary("后面如果你要继续改这个文档，直接告诉我想补哪一段就行。") == ""
