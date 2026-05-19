from response_composer import ResponseComposer


def test_phase112_response_composer_prefers_completed_summary() -> None:
    composer = ResponseComposer()

    plan = composer.response_plan_for_action_status(
        facts={
            "status": "completed_with_evidence",
            "action_label": "卸载 QQ",
            "target": "QQ",
            "evidence_summary": "卸载 QQ这一步已经开始推进了，我会按实际结果继续汇报。",
            "completed_summary": "卸载 QQ",
        }
    )

    assert "当前结果是：卸载 QQ。" in (plan.plain_text or "")
    assert "结果和记录都能回看" not in (plan.plain_text or "")
    assert plan.structured_payload["action_status"]["completed_summary"] == "卸载 QQ"


def test_phase112_response_composer_falls_back_to_label_when_evidence_is_progress() -> None:
    composer = ResponseComposer()

    plan = composer.response_plan_for_action_status(
        facts={
            "status": "completed_with_evidence",
            "action_label": "卸载 QQ",
            "target": "QQ",
            "evidence_summary": "卸载 QQ这一步已经开始推进了，我会按实际结果继续汇报。",
        }
    )

    assert "当前结果是：卸载 QQ。" in (plan.plain_text or "")
