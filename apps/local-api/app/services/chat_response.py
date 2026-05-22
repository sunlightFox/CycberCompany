from __future__ import annotations

import re
from typing import Any

from core_types import ResponsePlan
from response_composer import canonical_action_status, normalize_action_status_semantics, mirrored_status_payload

from app.services.chat_safety import ChatVisibleOutputFilter, response_filter_payload
from app.services.chat_turn_input_facts import structured_summary_chat_request
from app.services.chat_visible_guard import preserve_visible_reply_contract, visible_text_guard


def _scrub_visible_response_plan_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"trace_id", "prompt_snapshot_id", "tool_call_id", "approval_id"}:
                continue
            cleaned[key] = _scrub_visible_response_plan_payload(item)
        return cleaned
    if isinstance(value, list):
        return [_scrub_visible_response_plan_payload(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\btrc_[A-Za-z0-9_-]+", "审计记录", value)
        value = re.sub(r"\bapr_[A-Za-z0-9_-]+", "确认编号", value)
        value = re.sub(r"\b(?:toolcall|tool_call|call)_[A-Za-z0-9_-]+", "工具记录", value)
    return value


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

    def _repair_structured_summary_text(
        self,
        text: str,
        *,
        structured_payload: dict[str, Any],
    ) -> str:
        user_text = str(structured_payload.get("current_user_text") or "")
        if not user_text or not structured_summary_chat_request(user_text):
            return text
        try:
            preference = _summary_structure_preference(user_text, structured_payload)
            if preference == "heading_table_conclusion":
                return _ensure_heading_table_conclusion(text)
            if preference == "heading_two_paragraphs":
                return _ensure_heading_two_paragraphs(text)
            if preference == "heading_bullets_conclusion":
                return _ensure_heading_bullets_conclusion(text)
            if preference == "heading_numbered_list":
                return _ensure_heading_numbered_list(text)
            if preference == "section_headers_paragraphs":
                return _ensure_section_headers_paragraphs(text, user_text=user_text)
        except Exception:
            return text
        return text

    def normalize_plan_text(self, plan: ResponsePlan, fallback_text: str) -> dict[str, str]:
        structured_payload = dict(plan.structured_payload or {})
        user_text = str(structured_payload.get("current_user_text") or "")
        summary_text = self.visible_text(plan.summary or fallback_text)
        plain_text = self.visible_text(plan.plain_text or fallback_text)
        if user_text:
            from app.services.chat_model_execution import _repair_irrelevant_model_reply

            repaired_summary = _repair_irrelevant_model_reply(user_text, summary_text)
            if repaired_summary is not None:
                summary_text = self.visible_text(repaired_summary)
            repaired_plain = _repair_irrelevant_model_reply(user_text, plain_text)
            if repaired_plain is not None:
                plain_text = self.visible_text(repaired_plain)
        summary_text = self._repair_structured_summary_text(
            summary_text,
            structured_payload=structured_payload,
        )
        plain_text = self._repair_structured_summary_text(
            plain_text,
            structured_payload=structured_payload,
        )
        if user_text and not structured_summary_chat_request(user_text):
            summary_text = preserve_visible_reply_contract(summary_text, user_text=user_text)
            plain_text = preserve_visible_reply_contract(plain_text, user_text=user_text)
        return {
            "summary": summary_text,
            "plain_text": plain_text,
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
        authoritative_text_provided: bool = False,
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
        memory_recall_summary = dict(structured_payload.get("memory_recall_summary") or {})
        prompt_contract_metadata = self._prompt_contract_metadata(plan)
        phase111_completion_semantics = dict(
            structured_payload.get("phase111_completion_semantics") or {}
        )
        if (
            phase111_completion_semantics.get("delivery_status") == "delivered"
            and not phase111_completion_semantics.get("status")
        ):
            phase111_completion_semantics["status"] = "completed_with_evidence"
        visible_status_hint = (
            plan.visible_status_hint
            or str(phase111_completion_semantics.get("status") or "")
            or str(action_status_semantics.get("status") or "")
            or None
        )
        channel_render_overrides = dict(plan.channel_render_overrides or {})
        reply_blocks = list(plan.reply_blocks or plan.sections or [])
        sections = list(plan.sections or reply_blocks)
        if authoritative_text_provided and text_update["plain_text"]:
            sections = [{"kind": "summary", "text": text_update["plain_text"]}]
            reply_blocks = list(sections)

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
        structured_payload["memory_recall_summary"] = memory_recall_summary
        structured_payload["phase111_completion_semantics"] = phase111_completion_semantics
        structured_payload["prompt_contract_metadata"] = prompt_contract_metadata
        structured_payload["response_contract"] = {
            "visible_authority": "response_plan_plain_text",
            "visible_layer_fields": list(ResponsePlan.VISIBLE_LAYER_FIELDS),
            "internal_layer_fields": list(ResponsePlan.INTERNAL_LAYER_FIELDS),
        }
        structured_payload = _scrub_visible_response_plan_payload(structured_payload)
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
            "memory_recall_summary": memory_recall_summary,
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
            structured_payload = dict(plan.structured_payload or {})
            user_text = str(structured_payload.get("current_user_text") or "")
            visible_main_text = self.visible_text(authoritative_text or fallback_text)
            if user_text:
                from app.services.chat_model_execution import _repair_irrelevant_model_reply

                repaired_main = _repair_irrelevant_model_reply(user_text, visible_main_text)
                if repaired_main is not None:
                    visible_main_text = self.visible_text(repaired_main)
            visible_main_text = self._repair_structured_summary_text(
                visible_main_text,
                structured_payload=structured_payload,
            )
            if user_text and not structured_summary_chat_request(user_text):
                visible_main_text = preserve_visible_reply_contract(
                    visible_main_text,
                    user_text=user_text,
                )
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
            authoritative_text_provided=authoritative_text is not None,
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


def _summary_structure_preference(
    user_text: str,
    structured_payload: dict[str, Any],
) -> str | None:
    raw = str(user_text or "")
    session_context = structured_payload.get("session_context")
    session_context = session_context if isinstance(session_context, dict) else {}
    profile_block = str(session_context.get("stable_user_profile_block") or "")
    recent_messages = session_context.get("relevant_recent_messages")
    recent_text = ""
    if isinstance(recent_messages, list):
        recent_parts: list[str] = []
        for item in recent_messages[-4:]:
            if isinstance(item, dict):
                recent_parts.append(str(item.get("content_text") or item.get("model_safe_content_text") or ""))
        recent_text = "\n".join(part for part in recent_parts if part)
    conversation_summary = str(session_context.get("current_conversation_summary") or "")
    raw_lower = raw.lower()
    recent_combined = f"{recent_text}\n{conversation_summary}\n{raw}"
    combined = f"{recent_text}\n{conversation_summary}\n{profile_block}\n{raw}"
    combined_lower = combined.lower()
    if all(marker in raw for marker in ("标题", "三条", "结论")):
        return "heading_bullets_conclusion"
    if "不要表格" in raw and any(marker in raw for marker in ("两段", "段落", "小标题")):
        return "heading_two_paragraphs"
    if any(marker in raw for marker in ("小标题", "每个小标题下一段", "每个标题下一段")):
        return "section_headers_paragraphs"
    if any(marker in raw for marker in ("编号列表", "编号", "三条", "三点", "要点")) or any(
        marker in raw_lower for marker in ("numbered list", "numbered", "bullet")
    ):
        return "heading_numbered_list"
    if "不要表格" in recent_combined and any(marker in recent_combined for marker in ("两段", "段落", "小标题")):
        return "heading_two_paragraphs"
    if any(marker in recent_combined for marker in ("小标题", "每个小标题下一段", "每个标题下一段")):
        return "section_headers_paragraphs"
    if all(marker in combined for marker in ("先标题", "表格")) and any(
        marker in combined for marker in ("最后一段结论", "最后一段总结", "结论段落")
    ):
        return "heading_table_conclusion"
    if all(marker in combined for marker in ("标题", "表格", "结论段落")):
        return "heading_table_conclusion"
    if all(marker in combined for marker in ("标题", "两段")) and "段落" in combined:
        return "heading_two_paragraphs"
    if "不要表格" in combined and any(marker in combined for marker in ("两段", "段落", "小标题")):
        return "heading_two_paragraphs"
    if all(marker in combined for marker in ("标题", "三条", "结论")):
        return "heading_bullets_conclusion"
    if any(marker in combined for marker in ("小标题", "每个小标题下一段", "每个标题下一段")):
        return "section_headers_paragraphs"
    if any(marker in combined for marker in ("编号列表", "编号", "三条", "三点", "要点")) or any(
        marker in combined_lower for marker in ("numbered list", "numbered", "bullet")
    ):
        return "heading_numbered_list"
    return None


def _strip_summary_scaffolding(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    lines = [line.rstrip() for line in cleaned.splitlines()]
    kept: list[str] = []
    skip_patterns = (
        "按你",
        "按我",
        "如果你要",
        "我也可以",
        "可以总结成",
        "整理成一版",
    )
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if any(marker in stripped for marker in skip_patterns):
            continue
        kept.append(stripped)
    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _extract_heading_and_body(text: str) -> tuple[str, str]:
    cleaned = _strip_summary_scaffolding(text)
    if not cleaned:
        return "总结", ""
    lines = cleaned.splitlines()
    first = lines[0].strip()
    if first.startswith("#"):
        return first.lstrip("#").strip() or "总结", "\n".join(lines[1:]).strip()
    if first.startswith("**") and first.endswith("**") and len(first) > 4:
        return first.strip("* ").strip() or "总结", "\n".join(lines[1:]).strip()
    title = "总结"
    m = re.search(r"([A-Za-z][A-Za-z0-9/+ -]{2,}|[\u4e00-\u9fff]{2,16})", first)
    if m:
        title = m.group(1).strip("：: ")
    return title, cleaned


def _body_sentences(text: str) -> list[str]:
    normalized = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？!?])\s+|\n+", normalized)
    sentences = [part.strip(" -") for part in parts if part.strip(" -")]
    return sentences


def _table_rows_from_text(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    normalized = str(text or "")
    normalized = normalized.replace("**", "")
    for match in re.finditer(r"(?:^|\s)(\d+)\.\s*([^：:\n-]+)[：:\-]\s*([^\n]+)", normalized):
        label = match.group(2).strip()
        detail = match.group(3).strip()
        if label and detail:
            rows.append((label, detail))
    for line in normalized.splitlines():
        stripped = line.strip(" -")
        if not stripped or stripped.startswith("#") or "|" in stripped:
            continue
        if "：" in stripped:
            left, right = stripped.split("：", 1)
        elif ":" in stripped:
            left, right = stripped.split(":", 1)
        else:
            continue
        left = re.sub(r"^\d+\.\s*", "", left).strip()
        right = right.strip()
        if left and right and len(left) <= 24:
            rows.append((left, right))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row not in seen:
            seen.add(row)
            deduped.append(row)
    return deduped[:4]


def _build_markdown_table(rows: list[tuple[str, str]]) -> str:
    header = "| 项目 | 说明 |\n| --- | --- |"
    body = "\n".join(f"| {label} | {detail} |" for label, detail in rows)
    return f"{header}\n{body}"


def _ensure_heading_table_conclusion(text: str) -> str:
    if "|" in text and text.lstrip().startswith("#"):
        return text
    title, body = _extract_heading_and_body(text)
    rows = _table_rows_from_text(body)
    sentences = _body_sentences(body)
    if not rows:
        rows = [(f"要点{i + 1}", sentence) for i, sentence in enumerate(sentences[:3])]
    conclusion = sentences[-1] if sentences else "结论：按当前偏好整理为标题、表格和收尾总结。"
    parts = [f"# {title}", "", _build_markdown_table(rows), "", conclusion]
    return "\n".join(parts).strip()


def _ensure_heading_two_paragraphs(text: str) -> str:
    title, body = _extract_heading_and_body(text)
    if "|" in body:
        body = re.sub(r"^\|.*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"\n{2,}", "\n", body).strip()
    sentences = _body_sentences(body)
    if not sentences:
        sentences = [line.strip() for line in body.splitlines() if line.strip()]
    if len(sentences) <= 2:
        first = sentences[0] if sentences else body.strip()
        second = sentences[1] if len(sentences) > 1 else ""
    else:
        split_index = max(1, len(sentences) // 2)
        first = "".join(sentences[:split_index]).strip()
        second = "".join(sentences[split_index:]).strip()
    paragraphs = [part for part in (first, second) if part]
    if len(paragraphs) == 1:
        paragraphs.append(paragraphs[0])
    return f"# {title}\n\n{paragraphs[0]}\n\n{paragraphs[1]}".strip()


def _ensure_heading_numbered_list(text: str) -> str:
    title, body = _extract_heading_and_body(text)
    sentences = _body_sentences(body)
    if not sentences:
        sentences = [line.strip(" -") for line in body.splitlines() if line.strip()]
    items: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s+", stripped):
            items.append(re.sub(r"^\d+\.\s*", "", stripped))
    if not items:
        rows = _table_rows_from_text(body)
        if rows:
            items = [f"{label}：{detail}" for label, detail in rows]
    if not items:
        items = sentences[:3]
    numbered = "\n".join(f"{index}. {item}" for index, item in enumerate(items[:3], start=1))
    return f"# {title}\n\n{numbered}".strip()


def _ensure_heading_bullets_conclusion(text: str) -> str:
    title, body = _extract_heading_and_body(text)
    body = re.sub(r"(?m)^\d+\.\s*", "", body)
    sentences = _body_sentences(body)
    if not sentences:
        sentences = [line.strip(" -*") for line in body.splitlines() if line.strip()]
    bullets = sentences[:3]
    if len(bullets) < 3:
        while len(bullets) < 3:
            bullets.append(bullets[-1] if bullets else "待补充。")
    conclusion = "".join(sentences[3:]).strip() if len(sentences) > 3 else (sentences[-1] if sentences else "结论待补充。")
    bullet_block = "\n".join(f"- {item}" for item in bullets[:3])
    return f"# {title}\n\n{bullet_block}\n\n{conclusion}".strip()


def _extract_requested_headers(user_text: str) -> list[str]:
    matches = re.findall(r"[“\"`]?([\u4e00-\u9fffA-Za-z]{1,12})[”\"`]?", user_text)
    headers: list[str] = []
    allowed = {"背景", "现状", "风险", "结论", "依据", "已知", "未知", "下一步"}
    for match in matches:
        if match in allowed and match not in headers:
            headers.append(match)
    return headers[:4]


def _ensure_section_headers_paragraphs(text: str, *, user_text: str) -> str:
    headers = _extract_requested_headers(user_text)
    if not headers:
        headers = ["背景", "现状", "风险"]
    body = _strip_summary_scaffolding(text)
    body = re.sub(r"(?m)^#{1,6}\s+", "", body)
    body = "\n".join(
        line for line in body.splitlines()
        if line.strip() and line.strip() not in headers
    )
    rows = _table_rows_from_text(body)
    sentences = _body_sentences(body)
    if rows:
        chunks = [detail for _, detail in rows]
    else:
        chunks = sentences
    cleaned_chunks = []
    for chunk in chunks:
        candidate = chunk.strip()
        candidate = re.sub(r"^#{1,6}\s*", "", candidate).strip()
        if candidate and candidate not in headers:
            cleaned_chunks.append(candidate)
    chunks = cleaned_chunks
    while len(chunks) < len(headers):
        chunks.append(chunks[-1] if chunks else "待补充。")
    parts: list[str] = []
    for index, header in enumerate(headers):
        content = chunks[index].strip()
        if not content:
            content = "待补充。"
        parts.extend([f"## {header}", "", content, ""])
    return "\n".join(parts).strip()
