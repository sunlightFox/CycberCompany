from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
REAL_BATCH_PATH = ROOT / "docs/测试/聊天主链路/2026-05-03-wechat-real-scenarios/run_wechat_real_scenarios.py"
CHECK_SCRIPT_PATH = ROOT / "scripts/check.ps1"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_phase68_quality_gate_tracks_prompt_versions_and_hash_only_sections() -> None:
    module = _load_module("phase68_quality_batch", REAL_BATCH_PATH)

    response_plan = {
        "structured_payload": {
            "scenario": "wechat_chat_main_chain",
            "voice_policy_version": "chat_voice.openclaw_hermes.v4",
            "prompt_assembly_version": "chat_prompt_assembly.openclaw_hermes.v4",
            "prompt_snapshot_id": "psnap_phase68",
            "prompt_section_ids": [
                "stable.soul",
                "stable.behavior",
                "current.user_message",
            ],
            "prompt_sections": [
                {
                    "section_id": "stable.soul",
                    "layer": "stable_system",
                    "content_hash": "sha256:soul",
                }
            ],
            "chat_quality_shadow": {
                "version": "chat_quality_shadow.openclaw_hermes.v1",
                "advisory_only": True,
                "conversation_understanding": {
                    "primary_scene": "deep_chat",
                    "quality_dimensions": ["deep_chat_depth"],
                },
                "dialogue_state": {"turn_continuity": "standalone"},
                "response_policy": {"opening_style": "natural_direct"},
                "policy_advisory_gate": {
                    "eligible_for_policy_advisory": True,
                    "eligibility_reason": "eligible",
                    "eligible_scene": "casual_chat",
                },
                "response_policy_comparison": {
                    "comparison_enabled": True,
                    "policy_diffs": ["opening_style", "followthrough_mode"],
                    "safe_to_promote_hint": True,
                },
                "action_dialogue_mapping": {"action_status": "no_action"},
                "quality_eval": {"quality_tags": []},
                "promotion_candidate": True,
                "promotion_target": "casual_chat_opening",
                "promotion_blockers": [],
            },
        }
    }

    probe = module.quality_probe(  # type: ignore[attr-defined]
        turn={"status": "completed", "assistant_text": "这里给你三点：第一，质量矩阵看准确性；第二，看结构；第三，假完成要直接失败。"},
        trace={"spans": []},
        response_plan=response_plan,
        manual={},
        visible_reply="这里给你三点：第一，质量矩阵看准确性；第二，看结构；第三，假完成要直接失败。",
    )
    gate_status, gate_reasons = module.machine_gate_status(  # type: ignore[attr-defined]
        quality_verdict=str(probe.get("quality_verdict") or ""),
        quality_tags=list(probe.get("quality_tags") or []),
        visible_reply=str(probe.get("visible_reply") or "这里给你三点：第一，质量矩阵看准确性；第二，看结构；第三，假完成要直接失败。"),
        redaction_passed=True,
        has_turn=True,
        prompt_contract=probe,
        latency_slow=False,
    )

    evidence = module.CaseEvidence(  # type: ignore[attr-defined]
        case_id="phase68-001",
        case_title="phase68 gate",
        expected_text="",
        sent_text="",
        visible_reply="这里给你三点：第一，质量矩阵看准确性；第二，看结构；第三，假完成要直接失败。",
        reply_source="response_plan",
        attachment_understanding=None,
        revision_used=False,
        redaction_passed=True,
        result_status="PASS",
        result_reasons=[],
        collected_at="2026-05-05T00:00:00Z",
        channel_event=None,
        delivery_binding=None,
        turn={"status": "completed", "trace_id": "trc_phase68"},
        envelope=None,
        queue=None,
        recovery=None,
        compactions=None,
        trace={"spans": []},
        latency={
            "segments_ms": {
                "first_token_ms": 12,
                "turn_inside_ms": 24,
                "inbound_poll_ms": 6,
                "outbound_delivery_ms": 8,
                "observed_total_ms": 36,
            },
            "span_latencies_ms": [],
        },
        quality_probe={
            **probe,
            "gate_status": gate_status,
            "gate_reasons": gate_reasons,
        },
    )
    summary = module.summarize_evidences([evidence])  # type: ignore[attr-defined]

    assert probe["voice_policy_version"] == "chat_voice.openclaw_hermes.v4"
    assert probe["prompt_assembly_version"] == "chat_prompt_assembly.openclaw_hermes.v4"
    assert probe["prompt_sections_hash_only"] is True
    assert probe["prompt_sections_have_content"] is False
    assert probe["old_prompt_residual_terms"] == []
    assert probe["continuation_enabled"] is False
    assert probe["shadow_policy_gate_enabled"] is True
    assert probe["shadow_policy_gate_reason"] == "eligible"
    assert probe["shadow_policy_scene"] == "casual_chat"
    assert probe["shadow_policy_comparison_enabled"] is True
    assert probe["shadow_policy_diff_fields"] == ["opening_style", "followthrough_mode"]
    assert probe["shadow_policy_promotion_candidate"] is True
    assert probe["shadow_policy_promotion_target"] == "casual_chat_opening"
    assert "chat_quality_shadow" in response_plan["structured_payload"]
    assert "content" not in str(response_plan["structured_payload"]["chat_quality_shadow"])
    assert gate_status == "pass"
    assert gate_reasons == []
    assert summary["quality"]["gate_status_counts"]["pass"] == 1
    assert summary["quality"]["prompt_version_coverage"]["voice_policy_v4_coverage"] == 1.0
    assert summary["quality"]["prompt_version_coverage"]["prompt_assembly_v4_coverage"] == 1.0
    assert summary["quality"]["with_old_prompt_residual_terms"] == 0
    shadow_summary = summary["quality"]["shadow_policy"]
    assert shadow_summary["comparison_enabled_count"] == 1
    assert shadow_summary["promotion_candidate_count"] == 1
    assert shadow_summary["policy_diff_field_counts"]["opening_style"] == 1
    assert shadow_summary["promotion_target_counts"]["casual_chat_opening"] == 1


def test_phase68_shadow_policy_summary_tolerates_missing_payload() -> None:
    module = _load_module("phase68_quality_batch_shadow_missing", REAL_BATCH_PATH)
    probe = module.quality_probe(  # type: ignore[attr-defined]
        turn={"status": "completed", "assistant_text": "正常回复。"},
        trace={"spans": []},
        response_plan={"structured_payload": {}},
        manual={},
        visible_reply="正常回复。",
    )
    evidence = module.CaseEvidence(  # type: ignore[attr-defined]
        case_id="phase68-002",
        case_title="phase68 shadow missing",
        expected_text="",
        sent_text="",
        visible_reply="正常回复。",
        reply_source="response_plan",
        attachment_understanding=None,
        revision_used=False,
        redaction_passed=True,
        result_status="PASS",
        result_reasons=[],
        collected_at="2026-05-05T00:00:00Z",
        channel_event=None,
        delivery_binding=None,
        turn={"status": "completed", "trace_id": "trc_phase68_missing"},
        envelope=None,
        queue=None,
        recovery=None,
        compactions=None,
        trace={"spans": []},
        latency={"segments_ms": {}, "span_latencies_ms": []},
        quality_probe={**probe, "gate_status": "pass", "gate_reasons": []},
    )
    summary = module.summarize_evidences([evidence])  # type: ignore[attr-defined]
    shadow_summary = summary["quality"]["shadow_policy"]
    assert probe["shadow_policy_gate_enabled"] is False
    assert probe["shadow_policy_gate_reason"] == "missing"
    assert shadow_summary["shadow_seen_count"] == 1
    assert shadow_summary["comparison_enabled_count"] == 0
    assert shadow_summary["promotion_candidate_count"] == 0


def test_phase68_quality_gate_flags_residual_prompt_text_and_script_wiring() -> None:
    module = _load_module("phase68_quality_batch_fail", REAL_BATCH_PATH)
    gate_status, gate_reasons = module.machine_gate_status(  # type: ignore[attr-defined]
        quality_verdict="差",
        quality_tags=["internal_jargon", "false_done"],
        visible_reply="好的，我来继续处理。trace_id=trc_123 已完成。",
        redaction_passed=False,
        has_turn=True,
        prompt_contract={
            "prompt_sections_have_content": True,
            "voice_policy_version": None,
            "prompt_assembly_version": None,
        },
        latency_slow=True,
    )

    check_script = CHECK_SCRIPT_PATH.read_text(encoding="utf-8")

    assert gate_status == "fail"
    assert "quality_hard_failure" in gate_reasons or "redaction_failed" in gate_reasons
    assert "prompt_sections_have_content" in gate_reasons
    assert "prompt_version_missing" in gate_reasons
    assert "old_prompt_residual" in gate_reasons
    assert "2026-05-01-quality\\run_chat_main_chain_quality_regression_cases.py" in check_script
    assert "2026-05-03-wechat-50-scenarios\\run_wechat_50_quality_latency.py" in check_script
    assert "2026-05-03-wechat-real-scenarios\\run_wechat_real_scenarios.py" in check_script
