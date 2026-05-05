from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core_types import (
    AssetCategory,
    Attachment,
    ChatContentPart,
    ChatIngressMetadata,
    ChatTurnRequest,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.schemas.assets import AssetQueryRequest
from app.services.asset_broker import AssetBrokerService

DEDUPE_TTL_SECONDS = 300
DEFAULT_DEBOUNCE_MS = 1200
MAX_MODEL_SAFE_TEXT_CHARS = 12000


@dataclass(slots=True)
class NormalizedChatEnvelope:
    envelope_id: str
    content_parts: list[dict[str, Any]]
    context_refs: list[dict[str, Any]]
    model_safe_text: str
    dedupe_key: str
    ingress_metadata: dict[str, Any]
    normalized_summary: dict[str, Any] = field(default_factory=dict)
    raw_payload_redacted: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatIngressPlan:
    envelope: NormalizedChatEnvelope
    queue_policy: str
    queue_status: str
    duplicate_turn_id: str | None = None
    collect_turn_id: str | None = None


class ChatContentNormalizer:
    def __init__(
        self,
        *,
        asset_broker: AssetBrokerService | None,
        trace_service: TraceService,
    ) -> None:
        self._asset_broker = asset_broker
        self._trace = trace_service

    async def normalize(
        self,
        *,
        request: ChatTurnRequest,
        turn_id: str,
        conversation_id: str,
        trace_id: str,
        root_span_id: str | None,
    ) -> NormalizedChatEnvelope:
        span_id = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.CONTENT_NORMALIZE,
            name="normalize chat content",
            parent_span_id=root_span_id,
            input_data={
                "input_type": request.input.type,
                "attachment_count": len(request.attachments),
                "context_ref_count": len(request.context_refs),
            },
        )
        try:
            content_parts = self._content_parts(request)
            context_refs = [
                _normalize_context_ref(item.model_dump(mode="json"))
                for item in request.context_refs
            ]
            await self._validate_asset_refs(
                request=request,
                content_parts=content_parts,
                context_refs=context_refs,
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
            safe_text = self._model_safe_text(content_parts, context_refs)
            ingress_metadata = self._ingress_metadata(request.ingress_metadata)
            understanding_summary = _understanding_summary_from_payload(
                dict(ingress_metadata.get("raw_payload") or {})
            )
            dedupe_key = self._dedupe_key(
                request=request,
                content_parts=content_parts,
                context_refs=context_refs,
                model_safe_text=safe_text,
            )
            summary = {
                "content_part_count": len(content_parts),
                "context_ref_count": len(context_refs),
                "attachment_count": len(request.attachments),
                "part_types": _counts_by_type(content_parts),
                "context_ref_types": _counts_by_type(context_refs),
                "model_safe_text_chars": len(safe_text),
                "truncated": len(safe_text) >= MAX_MODEL_SAFE_TEXT_CHARS,
                "asset_refs_validated": any(
                    item.get("type") == "asset_ref" for item in content_parts
                )
                or any(item.get("type") == "asset" for item in context_refs),
                "collected_message_count": 1,
            }
            summary.update(understanding_summary)
            envelope = NormalizedChatEnvelope(
                envelope_id=new_id("env"),
                content_parts=content_parts,
                context_refs=context_refs,
                model_safe_text=safe_text,
                dedupe_key=dedupe_key,
                ingress_metadata=ingress_metadata,
                normalized_summary=summary,
                raw_payload_redacted=redact(request.model_dump(mode="json")),
            )
            await self._trace.end_span(
                span_id,
                output_data={
                    "envelope_id": envelope.envelope_id,
                    "dedupe_key": dedupe_key,
                    "normalized_summary": summary,
                },
            )
            return envelope
        except Exception as exc:
            await self._trace.end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error": str(redact(str(exc)))},
            )
            raise

    def _content_parts(self, request: ChatTurnRequest) -> list[dict[str, Any]]:
        parts = [item.model_dump(mode="json") for item in request.input.content_parts]
        if request.input.text:
            parts.insert(
                0,
                ChatContentPart(
                    type="text",
                    text=request.input.text,
                ).model_dump(mode="json"),
            )
        if not parts:
            raise AppError(
                code="VALIDATION_ERROR",
                message="聊天输入不能为空",
                status_code=422,
            )
        for attachment in request.attachments:
            parts.append(_part_from_attachment(attachment))
        return [redact(_normalize_part(item)) for item in parts]

    def _ingress_metadata(self, metadata: ChatIngressMetadata) -> dict[str, Any]:
        data = metadata.model_dump(mode="json")
        if data.get("debounce_ms") is None:
            data["debounce_ms"] = DEFAULT_DEBOUNCE_MS
        data["raw_payload"] = redact(data.get("raw_payload") or {})
        return data

    def _model_safe_text(
        self,
        content_parts: list[dict[str, Any]],
        context_refs: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []
        seen_attachment_refs: set[str] = set()
        for part in content_parts:
            part_type = str(part.get("type") or "text")
            if part_type in {"text", "image_summary", "audio_transcript", "file_extract"}:
                text = str(redact(part.get("text") or "")).strip()
                if text:
                    if part_type == "image_summary":
                        lines.append(_prefixed_model_line("图片内容线索", text))
                    elif part_type == "audio_transcript":
                        lines.append(_prefixed_model_line("语音内容线索", text))
                    elif part_type == "file_extract":
                        lines.append(_prefixed_model_line("文件内容摘录", text))
                    else:
                        lines.append(text)
                continue
            label = str(
                redact(part.get("name") or part.get("ref_id") or part.get("uri") or part_type)
            )
            ref_key = f"{part_type}:{label}"
            if ref_key in seen_attachment_refs:
                continue
            seen_attachment_refs.add(ref_key)
            if part_type == "image":
                lines.append(f"用户还附带了一张图片：{label}")
            elif part_type == "audio":
                lines.append(f"用户还附带了一段语音：{label}")
            elif part_type == "file":
                lines.append(f"用户还附带了一个文件：{label}")
            else:
                lines.append(f"用户还附带了一个{part_type}：{label}")
        for ref in context_refs:
            label = ref.get("label") or ref.get("ref_id") or ref.get("uri") or ref.get("type")
            lines.append(f"上下文参考 {ref.get('type')}：{redact(str(label))}")
        text = "\n".join(item for item in lines if item).strip()
        return text[:MAX_MODEL_SAFE_TEXT_CHARS]

    def _dedupe_key(
        self,
        *,
        request: ChatTurnRequest,
        content_parts: list[dict[str, Any]],
        context_refs: list[dict[str, Any]],
        model_safe_text: str,
    ) -> str:
        metadata = request.ingress_metadata
        if metadata.dedupe_key:
            return f"client:{_hash(metadata.dedupe_key)}"
        if metadata.channel_message_id:
            return f"channel:{metadata.channel}:{_hash(metadata.channel_message_id)}"
        payload = {
            "session_id": request.session_id,
            "member_id": request.member_id,
            "text": model_safe_text,
            "parts": [
                {
                    "type": item.get("type"),
                    "ref_id": item.get("ref_id"),
                    "uri": item.get("uri"),
                    "name": item.get("name"),
                }
                for item in content_parts
            ],
            "refs": [
                {
                    "type": item.get("type"),
                    "ref_id": item.get("ref_id"),
                    "uri": item.get("uri"),
                }
                for item in context_refs
            ],
        }
        return f"derived:{_hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))}"

    async def _validate_asset_refs(
        self,
        *,
        request: ChatTurnRequest,
        content_parts: list[dict[str, Any]],
        context_refs: list[dict[str, Any]],
        conversation_id: str,
        trace_id: str,
    ) -> None:
        if self._asset_broker is None:
            return
        asset_refs = [
            str(item.get("ref_id") or "")
            for item in content_parts
            if item.get("type") == "asset_ref" and item.get("ref_id")
        ]
        asset_refs.extend(
            str(item.get("ref_id") or "")
            for item in context_refs
            if item.get("type") == "asset" and item.get("ref_id")
        )
        for asset_id in sorted(set(asset_refs)):
            await self._asset_broker.query(
                AssetQueryRequest(
                    subject_type="member",
                    subject_id=request.member_id,
                    conversation_id=conversation_id,
                    asset_type=_asset_type_from_id(asset_id),
                    requested_actions=["read_summary"],
                    keywords=[asset_id],
                    context={"source": "chat_content_normalizer", "asset_id": asset_id},
                ),
                trace_id=trace_id,
                raise_on_denied=True,
            )


class ChatIngressService:
    def __init__(
        self,
        *,
        chat_repo: Any,
        normalizer: ChatContentNormalizer,
    ) -> None:
        self._chat_repo = chat_repo
        self._normalizer = normalizer

    async def prepare(
        self,
        *,
        request: ChatTurnRequest,
        turn_id: str,
        conversation_id: str,
        trace_id: str,
        root_span_id: str | None,
    ) -> ChatIngressPlan:
        envelope = await self._normalizer.normalize(
            request=request,
            turn_id=turn_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            root_span_id=root_span_id,
        )
        duplicate = await self._chat_repo.find_recent_envelope_by_dedupe_key(
            envelope.dedupe_key,
            now=utc_now_iso(),
            ttl_seconds=DEDUPE_TTL_SECONDS,
        )
        policy = str(envelope.ingress_metadata.get("queue_policy") or "immediate")
        collect_turn_id = None
        if not duplicate and policy == "collect":
            existing = await self._chat_repo.find_collectable_envelope(
                session_id=request.session_id,
                member_id=request.member_id,
                conversation_id=conversation_id,
                now=utc_now_iso(),
                debounce_ms=_debounce_ms(envelope.ingress_metadata),
            )
            collect_turn_id = existing["turn_id"] if existing else None
        return ChatIngressPlan(
            envelope=envelope,
            queue_policy=policy,
            queue_status="superseded" if duplicate or collect_turn_id else "queued",
            duplicate_turn_id=duplicate["turn_id"] if duplicate else None,
            collect_turn_id=collect_turn_id,
        )

    def merge_envelopes(
        self,
        existing: dict[str, Any],
        incoming: NormalizedChatEnvelope,
    ) -> NormalizedChatEnvelope:
        content_parts = [
            *(existing.get("content_parts") or []),
            *incoming.content_parts,
        ]
        context_refs = _dedupe_refs(
            [
                *(existing.get("context_refs") or []),
                *incoming.context_refs,
            ]
        )
        previous_text = str(existing.get("model_safe_text") or "").strip()
        merged_text = "\n".join(
            item for item in [previous_text, incoming.model_safe_text.strip()] if item
        )[:MAX_MODEL_SAFE_TEXT_CHARS]
        previous_summary = dict(existing.get("normalized_summary") or {})
        incoming_summary = dict(incoming.normalized_summary)
        previous_count = int(previous_summary.get("collected_message_count") or 1)
        summary = {
            **incoming_summary,
            "content_part_count": len(content_parts),
            "context_ref_count": len(context_refs),
            "part_types": _counts_by_type(content_parts),
            "context_ref_types": _counts_by_type(context_refs),
            "model_safe_text_chars": len(merged_text),
            "truncated": (
                len(merged_text) >= MAX_MODEL_SAFE_TEXT_CHARS
                or bool(previous_summary.get("truncated"))
                or bool(incoming_summary.get("truncated"))
            ),
            "asset_refs_validated": bool(previous_summary.get("asset_refs_validated"))
            or bool(incoming_summary.get("asset_refs_validated")),
            "collected_message_count": previous_count + 1,
            "debounce_collected": True,
        }
        summary.update(_merge_understanding_summary(previous_summary, incoming_summary))
        ingress_metadata = {
            **dict(existing.get("ingress_metadata") or {}),
            **incoming.ingress_metadata,
            "collected_message_count": previous_count + 1,
            "collected_envelope_ids": [
                *list(
                    (existing.get("ingress_metadata") or {}).get("collected_envelope_ids")
                    or [existing.get("envelope_id")]
                ),
                incoming.envelope_id,
            ],
        }
        raw_payload = {
            "collected": [
                existing.get("raw_payload_redacted") or {},
                incoming.raw_payload_redacted,
            ]
        }
        return NormalizedChatEnvelope(
            envelope_id=str(existing["envelope_id"]),
            content_parts=content_parts,
            context_refs=context_refs,
            model_safe_text=merged_text,
            dedupe_key=str(existing["dedupe_key"]),
            ingress_metadata=redact(ingress_metadata),
            normalized_summary=redact(summary),
            raw_payload_redacted=redact(raw_payload),
        )


def _part_from_attachment(attachment: Attachment) -> dict[str, Any]:
    content_type = str(attachment.content_type or "application/octet-stream")
    if content_type.startswith("image/"):
        part_type = "image"
    elif content_type.startswith("audio/"):
        part_type = "audio"
    else:
        part_type = "file"
    return ChatContentPart(
        type=part_type,
        uri=attachment.uri,
        name=attachment.name,
        content_type=content_type,
        ref_id=attachment.attachment_id,
        metadata=attachment.metadata,
    ).model_dump(mode="json")


def _prefixed_model_line(prefix: str, text: str) -> str:
    if text.startswith(f"{prefix}：") or text.startswith(f"{prefix}:"):
        return text
    if prefix == "语音内容线索" and text.startswith("语音转成文字："):
        return text
    return f"{prefix}：{text}"


def _normalize_part(part: dict[str, Any]) -> dict[str, Any]:
    data = dict(part)
    data["metadata"] = dict(data.get("metadata") or {})
    if data.get("uri"):
        data["uri"] = str(redact(str(data["uri"])))
    if data.get("text"):
        data["text"] = str(redact(str(data["text"])))
    return data


def _normalize_context_ref(ref: dict[str, Any]) -> dict[str, Any]:
    data = dict(ref)
    data["metadata"] = dict(data.get("metadata") or {})
    if data.get("uri"):
        data["uri"] = str(redact(str(data["uri"])))
    if data.get("label"):
        data["label"] = str(redact(str(data["label"])))
    return redact(data)


def _counts_by_type(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _understanding_summary_from_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    raw = raw_payload.get("multimodal_understanding")
    if not isinstance(raw, dict):
        return {}
    understood = _safe_int(raw.get("understood_attachment_count"))
    degraded = _safe_int(raw.get("degraded_attachment_count"))
    memory_candidates = _safe_int(raw.get("memory_candidate_count"))
    return {
        "understanding_status": str(raw.get("status") or _understanding_status_from_counts(
            understood,
            degraded,
        )),
        "understood_attachment_count": understood,
        "degraded_attachment_count": degraded,
        "memory_candidate_count": memory_candidates,
    }


def _merge_understanding_summary(
    previous: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    understood = _safe_int(previous.get("understood_attachment_count")) + _safe_int(
        incoming.get("understood_attachment_count")
    )
    degraded = _safe_int(previous.get("degraded_attachment_count")) + _safe_int(
        incoming.get("degraded_attachment_count")
    )
    memory_candidates = _safe_int(previous.get("memory_candidate_count")) + _safe_int(
        incoming.get("memory_candidate_count")
    )
    if not understood and not degraded and not memory_candidates:
        return {}
    return {
        "understanding_status": _understanding_status_from_counts(understood, degraded),
        "understood_attachment_count": understood,
        "degraded_attachment_count": degraded,
        "memory_candidate_count": memory_candidates,
    }


def _understanding_status_from_counts(understood: int, degraded: int) -> str:
    if understood and degraded:
        return "mixed"
    if understood:
        return "understood"
    if degraded:
        return "degraded"
    return "none"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _debounce_ms(metadata: dict[str, Any]) -> int:
    try:
        value = int(metadata.get("debounce_ms") or DEFAULT_DEBOUNCE_MS)
    except (TypeError, ValueError):
        return DEFAULT_DEBOUNCE_MS
    return max(0, min(value, 30000))


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for ref in refs:
        key = json.dumps(
            {
                "type": ref.get("type"),
                "ref_id": ref.get("ref_id"),
                "uri": ref.get("uri"),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _asset_type_from_id(asset_id: str) -> AssetCategory | None:
    lowered = asset_id.lower()
    for category in AssetCategory:
        if lowered.startswith(category.value) or f"_{category.value}" in lowered:
            return category
    return None
