from __future__ import annotations

from typing import Any

from core_types import ResponsePlan
from response_composer import canonical_action_status, normalize_action_status_semantics, mirrored_status_payload

from app.services.chat_safety import ChatVisibleOutputFilter, response_filter_payload
from app.services.chat_visible_guard import visible_text_guard


class ChatResponseCoordinator:
    """Centralizes visible chat output filtering and response-plan text cleanup."""

    def begin_visible_stream(self) -> ChatVisibleOutputFilter:
        return ChatVisibleOutputFilter()

    def filter_text(self, text: str) -> tuple[str, dict[str, Any]]:
        return ChatVisibleOutputFilter.filter_text(text)

    def merge_filter(
        self,
        response_filter: dict[str, Any] | None,
        final_filter: dict[str, Any],
    ) -> dict[str, Any]:
        merged = {
            **response_filter_payload(response_filter),
            "final_guard": final_filter,
        }
        merged["visible_text"] = str(
            final_filter.get("visible_text")
            or merged.get("visible_text")
            or ""
        )
        merged["filtered_segments"] = [
            *list(merged.get("filtered_segments") or []),
            *[
                item
                for item in list(final_filter.get("filtered_segments") or [])
                if item not in list(merged.get("filtered_segments") or [])
            ],
        ]
        merged["suppression_reason_codes"] = sorted(
            {
                *[str(item) for item in merged.get("suppression_reason_codes") or []],
                *[str(item) for item in final_filter.get("suppression_reason_codes") or []],
            }
        )
        return merged

    def visible_text(self, text: str) -> str:
        return visible_text_guard(text)

    def normalize_plan_text(self, plan: ResponsePlan, fallback_text: str) -> dict[str, str]:
        return {
            "summary": self.visible_text(plan.summary or fallback_text),
            "plain_text": self.visible_text(plan.plain_text or fallback_text),
        }

    def _prompt_contract_metadata(self, plan: ResponsePlan) -> dict[str, Any]:
        structured = dict(plan.structured_payload or {})
        prompt_keys = (
            "voice_policy_version",
            "prompt_assembly_version",
            "prompt_snapshot_id",
            "stable_prompt_hash",
            "dynamic_context_hash",
            "trusted_context_hash",
            "untrusted_context_hash",
            "history_context_hash",
            "current_message_hash",
            "prompt_section_ids",
            "prompt_sections",
            "prompt_profile",
            "prompt_mode",
            "prompt_assembly",
        )
        metadata = dict(plan.prompt_contract_metadata or {})
        for key in prompt_keys:
            if key in structured and key not in metadata:
                metadata[key] = structured.get(key)
        return metadata

    def _finalize_layered_payload(
        self,
        plan: ResponsePlan,
        *,
        response_filter: dict[str, Any],
        text_update: dict[str, str],
    ) -> dict[str, Any]:
        structured_payload = dict(plan.structured_payload or {})
        normalized_filter = response_filter_payload(response_filter or plan.response_filter)
        response_quality_guard = dict(
            plan.response_quality_guard
            or structured_payload.get("response_quality_guard")
            or structured_payload.get("quality_guard")
            or {}
        )
        route_semantics = dict(plan.route_semantics or structured_payload.get("route_semantics") or {})
        action_status_semantics = normalize_action_status_semantics(
            structured_payload.get("action_status_semantics")
            or structured_payload.get("action_status")
            or plan.task_status
            or structured_payload.get("tool_result_context")
            or structured_payload.get("task_status_semantics")
            or structured_payload.get("tool_status_semantics")
            or {},
            default_status="requested",
            scope=str(
                structured_payload.get("action_status_scope")
                or (
                    "direct_tool"
                    if structured_payload.get("tool_result_context")
                    else "workflow_summary"
                )
            ),
        )
        task_status_semantics = dict(
            plan.task_status_semantics
            or structured_payload.get("task_status_semantics")
            or plan.task_status
            or structured_payload.get("task_status")
            or {}
        )
        tool_status_semantics = dict(
            plan.tool_status_semantics
            or structured_payload.get("tool_status_semantics")
            or structured_payload.get("tool_result_context")
            or {}
        )
        task_status_semantics = mirrored_status_payload(
            {
                **action_status_semantics,
                "task_ref": dict(task_status_semantics or {}).get("task_ref")
                or {
                    "task_id": task_status_semantics.get("task_id"),
                    "status": task_status_semantics.get("status"),
                    "mode": task_status_semantics.get("mode"),
                },
            },
            extra=task_status_semantics,
        )
        tool_status_semantics = mirrored_status_payload(
            {
                **action_status_semantics,
                "tool_ref": dict(tool_status_semantics or {}).get("tool_ref")
                or {
                    "tool_call_id": tool_status_semantics.get("tool_call_id"),
                    "tool_name": tool_status_semantics.get("tool_name"),
                },
            },
            extra=tool_status_semantics,
        )
        if isinstance(structured_payload.get("tool_result_context"), dict):
            structured_payload["tool_result_context"] = {
                **dict(structured_payload.get("tool_result_context") or {}),
                **tool_status_semantics,
            }
        memory_write_hints = dict(
            plan.memory_write_hints
            or structured_payload.get("memory_write_hints")
            or {}
        )
        prompt_contract_metadata = self._prompt_contract_metadata(plan)
        visible_status_hint = (
            plan.visible_status_hint
            or str(action_status_semantics.get("status") or "")
            or None
        )
        channel_render_overrides = dict(plan.channel_render_overrides or {})
        reply_blocks = list(plan.reply_blocks or plan.sections or [])
        sections = list(plan.sections or reply_blocks)

        if isinstance(structured_payload.get("action_dialogue"), dict):
            action_dialogue = dict(structured_payload.get("action_dialogue") or {})
            raw_status = action_dialogue.get("action_status")
            if raw_status:
                action_dialogue["action_status"] = canonical_action_status(raw_status, default="requested")
                structured_payload["action_dialogue"] = action_dialogue

        structured_payload["action_status_semantics"] = action_status_semantics
        structured_payload["response_filter"] = normalized_filter
        structured_payload["response_quality_guard"] = response_quality_guard
        structured_payload["route_semantics"] = route_semantics
        structured_payload["task_status_semantics"] = task_status_semantics
        structured_payload["tool_status_semantics"] = tool_status_semantics
        structured_payload["memory_write_hints"] = memory_write_hints
        structured_payload["prompt_contract_metadata"] = prompt_contract_metadata
        structured_payload["response_contract"] = {
            "visible_authority": "response_plan_plain_text",
            "visible_layer_fields": list(ResponsePlan.VISIBLE_LAYER_FIELDS),
            "internal_layer_fields": list(ResponsePlan.INTERNAL_LAYER_FIELDS),
        }
        normalized_filter["visible_text"] = text_update["plain_text"]

        return {
            **text_update,
            "sections": sections,
            "reply_blocks": reply_blocks,
            "visible_status_hint": visible_status_hint,
            "channel_render_overrides": channel_render_overrides,
            "response_filter": normalized_filter,
            "response_quality_guard": response_quality_guard,
            "route_semantics": route_semantics,
            "task_status_semantics": task_status_semantics,
            "tool_status_semantics": tool_status_semantics,
            "memory_write_hints": memory_write_hints,
            "prompt_contract_metadata": prompt_contract_metadata,
            "structured_payload": structured_payload,
        }

    def finalize_plan(
        self,
        plan: ResponsePlan,
        fallback_text: str,
        *,
        authoritative_text: str | None = None,
        response_filter: dict[str, Any] | None = None,
    ) -> ResponsePlan:
        if authoritative_text is not None:
            visible_main_text = self.visible_text(authoritative_text or fallback_text)
            text_update = {
                "summary": visible_main_text,
                "plain_text": visible_main_text,
            }
        else:
            text_update = self.normalize_plan_text(plan, fallback_text)
        layered_payload = self._finalize_layered_payload(
            plan,
            response_filter=response_filter_payload(response_filter or plan.response_filter),
            text_update=text_update,
        )
        return plan.model_copy(update=layered_payload)

    def final_text(
        self,
        plan: ResponsePlan,
        fallback_text: str,
        *,
        authoritative_text: str | None = None,
    ) -> str:
        finalized = self.finalize_plan(
            plan,
            fallback_text,
            authoritative_text=authoritative_text,
        )
        return finalized.plain_text
