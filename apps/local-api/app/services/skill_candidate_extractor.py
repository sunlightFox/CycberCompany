from __future__ import annotations

from typing import Any


class SkillCandidateExtractor:
    def extract_from_replay(self, replay: Any) -> list[dict[str, Any]]:
        tool_calls = list(getattr(replay, "tool_calls", []) or [])
        skill_runs = list(getattr(replay, "skill_runs", []) or [])
        artifacts = list(getattr(replay, "artifacts", []) or [])
        retry_plans = list(getattr(replay, "retry_plans", []) or [])
        final_result = dict(getattr(replay, "final_result", {}) or {})
        goal = _text(getattr(getattr(replay, "task", None), "goal", None)) or _text(
            final_result.get("summary")
        )

        candidates: list[dict[str, Any]] = []
        if len(tool_calls) >= 2:
            full_tool_names = [
                str(call.tool_name) for call in tool_calls if getattr(call, "tool_name", None)
            ]
            steps = [
                {
                    "tool_name": call.tool_name,
                    "status": call.status,
                    "args_preview": dict(call.args_redacted or {}),
                }
                for call in tool_calls[:6]
            ]
            tool_names = full_tool_names[:6]
            candidate_type, classification_reason = _classify_candidate(full_tool_names)
            candidates.append(
                {
                    "candidate_type": candidate_type,
                    "source": "task_replay",
                    "goal": goal,
                    "tool_names": tool_names,
                    "steps": steps,
                    "required_tools": sorted(set(full_tool_names)),
                    "artifacts": [getattr(artifact, "display_name", None) for artifact in artifacts[:5]],
                    "failure_recovery": [_retry_summary(item) for item in retry_plans[:3]],
                    "acceptance": _acceptance_cases(goal, artifacts, final_result),
                    "confidence": "medium" if len(tool_calls) >= 3 else "low",
                    "workflow_signature": " -> ".join(full_tool_names[:8]),
                    "primary_tools": _primary_tools(full_tool_names),
                    "inputs": _collect_inputs(tool_calls),
                    "outputs": _collect_outputs(artifacts, final_result),
                    "preconditions": _preconditions(goal, tool_calls),
                    "recovery_hints": [_retry_summary(item) for item in retry_plans[:3]],
                    "classification_reason": classification_reason,
                }
            )
        if skill_runs:
            candidates.append(
                {
                    "candidate_type": "skill_run_replay",
                    "source": "task_replay",
                    "goal": goal,
                    "skill_ids": [str(item.get("skill_id")) for item in skill_runs[:5]],
                    "outcomes": [str(item.get("status")) for item in skill_runs[:5]],
                    "acceptance": _acceptance_cases(goal, artifacts, final_result),
                    "confidence": "medium",
                }
            )
        return candidates


def _classify_candidate(tool_names: list[str]) -> tuple[str, str]:
    lowered = [name.lower() for name in tool_names]
    has_office = any("office" in name or "document" in name or "sheet" in name for name in lowered)
    has_browser_mcp = any(name.startswith("browser.") or name.startswith("mcp.") for name in lowered)
    has_publish = any("publish" in name or "post" in name for name in lowered)
    if has_browser_mcp:
        return "browser_mcp_workflow", "browser/mcp tools present in replay"
    if has_publish:
        return "content_publish_workflow", "publishing tools present in replay"
    if has_office:
        return "office_workflow", "office/document tools present in replay"
    return "tool_chain", "multi-step tool chain without stronger workflow markers"


def _acceptance_cases(goal: str, artifacts: list[Any], final_result: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    if goal:
        cases.append({"case": "goal_preserved", "expected": goal[:120]})
    if artifacts:
        cases.append(
            {
                "case": "artifacts_created",
                "expected": [getattr(artifact, "display_name", None) for artifact in artifacts[:3]],
            }
        )
    if final_result:
        cases.append({"case": "final_result_present", "expected": bool(final_result)})
    return cases


def _retry_summary(item: Any) -> dict[str, Any]:
    return {
        "reason": _text(getattr(item, "trigger_reason", None)),
        "summary": _text(getattr(item, "summary", None)),
    }


def _collect_inputs(tool_calls: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for call in tool_calls[:6]:
        args = dict(getattr(call, "args_redacted", {}) or {})
        keys = sorted(str(key) for key in args.keys())
        items.append(
            {
                "tool_name": str(getattr(call, "tool_name", "")),
                "arg_keys": keys,
                "has_args": bool(keys),
            }
        )
    return items


def _collect_outputs(artifacts: list[Any], final_result: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = [
        {"type": "artifact", "name": getattr(artifact, "display_name", None)}
        for artifact in artifacts[:5]
        if getattr(artifact, "display_name", None)
    ]
    if final_result:
        outputs.append(
            {"type": "final_result", "keys": sorted(str(key) for key in final_result.keys())[:8]}
        )
    if not outputs:
        outputs.append({"type": "execution", "present": True})
    return outputs


def _preconditions(goal: str, tool_calls: list[Any]) -> list[str]:
    conditions: list[str] = []
    if goal:
        conditions.append(f"goal:{goal[:120]}")
    first_tool = getattr(tool_calls[0], "tool_name", None) if tool_calls else None
    if first_tool:
        conditions.append(f"starts_with:{first_tool}")
    return conditions


def _primary_tools(tool_names: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for name in tool_names:
        if name in seen:
            continue
        ordered.append(name)
        seen.add(name)
        if len(ordered) >= 3:
            break
    return ordered


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
