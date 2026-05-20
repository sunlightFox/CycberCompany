from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from brain.adapters import estimate_text_tokens
from core_types import (
    ErrorCode,
    MemoryConflictRecord,
    MemoryBlock,
    MemoryBlockItem,
    MemoryCandidate,
    MemoryExperienceRecord,
    MemoryItem,
    MemoryLayer,
    MemoryReuseFeedback,
    MemorySearchFilteredItem,
    MemorySearchHit,
    MemorySearchRankingItem,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from safety_service import SafetyService
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.memory_repo import MemoryRepository
from app.db.repositories.retrieval_repo import RetrievalRepository
from app.db.session import Database
from app.schemas.memory import (
    MemoryExperienceConsolidateResponse,
    MemoryExtractResponse,
    MemoryJobItem,
    MemoryReuseFeedbackRequest,
    MemorySearchApiRequest,
    MemorySearchApiResponse,
    MemoryUpdateRequest,
)
from app.services.audit import AuditEventService
from app.services.chat_turn_input_facts import (
    explicit_preference_recall_query,
    preference_application_request,
    structured_summary_chat_request,
)

DEFAULT_USER_ID = "user_local_owner"
MIN_WRITE_SCORE = 0.55
MEMORY_SEMANTIC_CONTRACT_VERSION = "phase107.memory_semantic_contract.v1"

REMEMBER_MARKERS = ("记住", "请记住", "以后", "我的偏好", "这个项目规则")
EXPLICIT_REMEMBER_PREFIXES = ("记住", "请记住", "以后", "我的偏好", "这个项目规则")
BLOCK_MARKERS = ("不要记", "别记", "不要再记", "别再记")
CORRECTION_MARKERS = ("改成", "不是", "以后不")
WORKER_ID = "memory_worker_local"
JOB_STALE_AFTER_MINUTES = 10
EXTRA_EXPLICIT_REMEMBER_PREFIXES = (
    "\u8bb0\u4f4f",
    "\u518d\u8bb0\u4f4f",
    "\u5e2e\u6211\u8bb0\u4f4f",
    "\u8bf7\u8bb0\u4f4f",
    "\u4ee5\u540e\u6309\u8fd9\u4e2a",
    "\u4ee5\u540e\u90fd\u6309\u8fd9\u4e2a",
)
EXTRA_CORRECTION_PREFIXES = (
    "\u4fee\u6b63",
    "\u7ea0\u6b63",
    "\u66f4\u65b0",
)
SECRET_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:token|password|secret|api[_-]?key)\b\s*(?:is|=|:)\s*\S+", re.I),
)


@dataclass(frozen=True)
class MemoryCommand:
    kind: str
    memory_kind: str
    layer: str
    summary: str
    score: float
    explicit: bool
    supersede_query: str | None = None
    review_required: bool = False


@dataclass(frozen=True)
class MemoryScore:
    final_score: float
    decision: str
    reason: str | None
    review_required: bool = False


@dataclass(frozen=True)
class MemoryRetrievalDiagnostics:
    selected_memory_ids: list[str]
    filtered: list[MemorySearchFilteredItem]
    ranking: list[MemorySearchRankingItem]


@dataclass(frozen=True)
class RerankResult:
    rows: list[dict[str, Any]]
    suppressed: list[dict[str, Any]]
    latency_ms: float


@dataclass
class MemoryCommandResult:
    handled: bool
    response_text: str | None = None
    candidates: list[MemoryCandidate] = field(default_factory=list)
    memories: list[MemoryItem] = field(default_factory=list)
    blocked: bool = False
    reason: str | None = None


class MemoryRerankService:
    scoring_policy = {
        "semantic_score": 0.24,
        "recency_score": 0.1,
        "source_reliability": 0.08,
        "explicitness": 0.08,
        "quality_score": 0.14,
        "reuse_score": 0.08,
        "version_stability": 0.09,
        "conflict_safety": 0.08,
        "sensitivity_penalty": 0.08,
        "conversation_relevance": 0.04,
        "member_scope": 0.03,
        "provider_quality": 0.01,
    }

    def rerank(
        self,
        rows: list[dict[str, Any]],
        *,
        request: MemorySearchApiRequest,
        member_id: str,
        limit: int,
    ) -> RerankResult:
        started = time.perf_counter()
        selected: list[dict[str, Any]] = []
        suppressed: list[dict[str, Any]] = []
        for row in rows:
            reason = _suppression_reason_for_memory(row, request=request)
            base_score = _semantic_score(row)
            if reason:
                suppressed.append(
                    _suppressed_item(
                        target_id=row["memory_id"],
                        reason=reason,
                        sensitivity=row.get("sensitivity"),
                        score=base_score,
                        metadata={
                            "retrieval_source": row.get("retrieval_source"),
                            "status": row.get("status"),
                            "validity": _memory_validity(row),
                        },
                    )
                )
                continue
            score_parts = {
                "semantic_score": base_score,
                "recency_score": _recency_score(row),
                "source_reliability": _source_reliability(row),
                "explicitness": _explicitness_score(row),
                "quality_score": _quality_score(row),
                "reuse_score": _reuse_score(row),
                "version_stability": _version_stability_score(row),
                "conflict_safety": _conflict_safety_score(row),
                "sensitivity_penalty": _sensitivity_score(row),
                "conversation_relevance": _conversation_score(row, request.conversation_id),
                "member_scope": _member_scope_score(row, member_id),
                "provider_quality": _provider_quality_score(row),
            }
            rerank_score = sum(
                score_parts[key] * weight for key, weight in self.scoring_policy.items()
            )
            conflict_notes = _memory_conflict_notes(row)
            selection_confidence = min(
                0.99,
                max(0.05, rerank_score * 0.7 + float(row.get("confidence", 0.5)) * 0.3),
            )
            selected.append(
                {
                    **row,
                    "rerank_score": round(rerank_score, 4),
                    "selection_confidence": round(selection_confidence, 4),
                    "conflict_notes": conflict_notes,
                    "requires_user_confirmation": bool(conflict_notes)
                    or selection_confidence < 0.38,
                    "selection_reason": [
                        *row.get("selection_reason", []),
                        "rerank_quality_score",
                        "rerank_quality_reuse_version",
                    ],
                }
            )
        selected.sort(
            key=lambda item: (
                float(item.get("rerank_score", 0.0)),
                float(item.get("confidence", 0.0)),
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        return RerankResult(rows=selected[:limit], suppressed=suppressed, latency_ms=latency_ms)


class MemoryService:
    def __init__(
        self,
        *,
        db: Database,
        repo: MemoryRepository,
        chat_repo: ChatRepository,
        member_repo: MemberRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
        vector_service: Any | None = None,
        retrieval_repo: RetrievalRepository | None = None,
        chat_run_ledger: Any | None = None,
        chat_hook_runtime: Any | None = None,
    ) -> None:
        self._db = db
        self._repo = repo
        self._chat = chat_repo
        self._members = member_repo
        self._trace = trace_service
        self._audit = audit_service
        self._vector = vector_service
        self._retrieval_repo = retrieval_repo
        self._chat_run_ledger = chat_run_ledger
        self._chat_hook_runtime = chat_hook_runtime
        self._reranker = MemoryRerankService()
        self._safety = SafetyService()
        self._background_tasks: set[asyncio.Task[int]] = set()

    def runtime_diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "memory_service",
            "memory_contract_version": "phase92.long_term_memory_recall.v1",
            "memory_semantic_contract_version": MEMORY_SEMANTIC_CONTRACT_VERSION,
            "cross_session_recall_enabled": True,
            "canonical_memory_classes": [
                "preference",
                "fact",
                "experience",
                "transient_working_state",
            ],
            "freshness_policy": [
                "exclude_stale",
                "prefer_fresh",
                "allow_superseded",
                "allow_expired",
            ],
            "supersede_policy": "latest_correction_wins",
            "recall_api": "/api/memory/search",
        }

    async def list_memories(
        self,
        *,
        member_id: str | None = None,
        status: str | None = None,
        layer: str | None = None,
        kind: str | None = None,
        memory_class: str | None = None,
        durability: str | None = None,
        freshness_state: str | None = None,
        sensitivity: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        rows = await self._repo.list_memory_items(
            member_id=member_id,
            status=status,
            layer=layer,
            kind=kind,
            memory_class=memory_class,
            durability=durability,
            freshness_state=freshness_state,
            sensitivity=sensitivity,
            query=query,
            limit=limit,
        )
        return [_memory_item(row) for row in rows]

    async def get_memory(self, memory_id: str) -> MemoryItem:
        row = await self._repo.get_memory_item(memory_id)
        if row is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在", status_code=404)
        return _memory_item(row)

    async def write_failure_advisory_memory(
        self,
        *,
        member_id: str,
        summary_text: str,
        source: dict[str, Any],
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> MemoryItem:
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        now = utc_now_iso()
        breakdown = {
            "value": 0.76,
            "clarity": 0.72,
            "stability": 0.68,
            "sensitivity": 0.88,
            "reuse": 0.71,
            "conflict_risk": 0.18,
        }
        candidate = await self._insert_candidate(
            organization_id=member["organization_id"],
            member_id=member_id,
            source=source,
            proposed_layer=MemoryLayer.PROCEDURAL.value,
            proposed_kind="failure_advisory",
            proposed_scope_type="member",
            proposed_scope_id=member_id,
            summary_text=summary_text,
            payload=payload,
            score={"quality_breakdown": breakdown, "quality_score": 0.72},
            final_score=0.72,
            sensitivity="low",
            decision="auto_written",
            decision_reason="phase94_failure_advisory",
            now=now,
        )
        return await self._insert_memory_from_candidate(
            _candidate_row(candidate),
            decision="auto_written",
            trace_id=trace_id,
            now=now,
            quality_score=0.72,
            quality_breakdown=breakdown,
            retention_policy="standard",
        )

    async def update_memory(
        self,
        memory_id: str,
        request: MemoryUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> MemoryItem:
        existing = await self._repo.get_memory_item(memory_id)
        if existing is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在", status_code=404)
        fields = request.model_dump(exclude_unset=True)
        if "summary_text" in fields and fields["summary_text"]:
            classification = self._safety.classify_chat_input(str(fields["summary_text"]))
            if classification.sensitivity_hits:
                raise AppError(
                    ErrorCode.MEMORY_POLICY_BLOCKED,
                    "记忆摘要包含敏感信息，不能写入长期记忆",
                    status_code=400,
                    details={"sensitivity_hits": classification.sensitivity_hits},
                )
            fields["summary_text"] = classification.redacted_text
            fields["normalized_summary"] = _normalize(classification.redacted_text)
            fields["content_hash"] = _hash_text(_normalize(classification.redacted_text))
        if "payload" in fields and fields["payload"] is not None:
            fields["payload"] = redact(fields["payload"])
        fields["updated_at"] = utc_now_iso()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_CORRECTION,
            "update memory",
            metadata={"memory_id": memory_id},
        )
        await self._repo.update_memory_item(memory_id, fields)
        await self._end_span(
            span_id,
            output_data={"memory_id": memory_id, "changed_fields": sorted(fields)},
        )
        await self._audit.write_event(
            actor_type="system",
            action="memory.updated",
            object_type="memory",
            object_id=memory_id,
            summary="记忆已更新",
            risk_level=RiskLevel.R1,
            payload={
                "before_summary": existing["summary_text"],
                "after_summary": fields.get("summary_text", existing["summary_text"]),
            },
            trace_id=trace_id,
        )
        return await self.get_memory(memory_id)

    async def search(
        self,
        request: MemorySearchApiRequest,
        *,
        default_member_id: str = "mem_xiaoyao",
        trace_id: str | None = None,
        turn_id: str | None = None,
    ) -> MemorySearchApiResponse:
        member_id = request.member_id or default_member_id
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        organization_id = member["organization_id"]
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_SEARCH,
            "search memory",
            input_data={"query": request.query},
            metadata={
                "member_id": member_id,
                "conversation_id": request.conversation_id,
                "include_sensitive": request.include_sensitive,
            },
        )
        try:
            rows: list[dict[str, Any]] = []
            retrieval_sources: set[str] = set()
            provider = "local" if self._vector is not None else None
            degraded_reason: str | None = None
            if self._vector is not None:
                vector_hits = await self._vector.search_text(
                    collection_name=f"memory_{organization_id}",
                    target_type="memory",
                    query=str(redact(request.query)),
                    limit=max(request.limit * 3, request.limit),
                    trace_id=trace_id,
                )
                for hit in vector_hits:
                    provider = str(hit.get("provider") or provider or "local")
                    hit_degraded_reason = hit.get("degraded_reason") or hit.get(
                        "privacy_block_reason"
                    )
                    degraded_reason = degraded_reason or hit_degraded_reason
                    row = await self._repo.get_memory_item(str(hit["target_id"]))
                    if row is None or not _memory_row_allowed(
                        row,
                        organization_id=organization_id,
                        member_id=member_id,
                        request=request,
                    ):
                        continue
                    rows.append(
                        {
                            **row,
                            "rank_score": float(hit["score"]),
                            "retrieval_source": "semantic_vector",
                            "selection_reason": [
                                *hit.get("selection_reason", []),
                                "active_memory",
                            ],
                            "provider": hit.get("provider", "local"),
                            "embedding_model": hit.get("embedding_model"),
                            "fallback_chain": hit.get("fallback_chain", []),
                            "degraded_reason": hit_degraded_reason,
                        }
                    )
                    retrieval_sources.add("semantic_vector")
                    if len(rows) >= request.limit:
                        break
            else:
                degraded_reason = "vector_service_unavailable"
            if len(rows) < request.limit:
                fts_rows = await self._repo.search_memory_items(
                    organization_id=organization_id,
                    member_id=member_id,
                    query=str(redact(request.query)),
                    limit=request.limit,
                    exclude_conversation_id=request.exclude_conversation_id,
                    include_cross_session=request.include_cross_session,
                    memory_classes=request.memory_classes,
                    durability_filter=request.durability_filter,
                    include_archived=request.include_archived,
                    include_sensitive=request.include_sensitive,
                    include_asset_scoped=request.include_asset_scoped,
                    asset_scope_ids=request.asset_scope_ids,
                )
                existing_ids = {row["memory_id"] for row in rows}
                for row in fts_rows:
                    if row["memory_id"] in existing_ids:
                        continue
                    if request.layers and MemoryLayer(row["layer"]) not in request.layers:
                        continue
                    source = "fts_fallback" if not retrieval_sources else "fts_supplement"
                    rows.append(
                        {
                            **row,
                            "retrieval_source": source,
                            "selection_reason": [
                                "fts_fallback" if source == "fts_fallback" else "fts_supplement",
                                "active_memory",
                            ],
                            "provider": provider,
                            "embedding_model": None,
                            "fallback_chain": ["fts"],
                            "degraded_reason": degraded_reason,
                        }
                    )
                    retrieval_sources.add(source)
                    if len(rows) >= request.limit:
                        break
            if not rows and _should_use_recent_fallback(request.query, request.intent):
                rows = await self._recent_active_fallback(
                    organization_id=organization_id,
                    member_id=member_id,
                    limit=request.limit,
                    memory_classes=request.memory_classes,
                    durability_filter=request.durability_filter,
                    include_sensitive=request.include_sensitive,
                    include_asset_scoped=request.include_asset_scoped,
                    asset_scope_ids=request.asset_scope_ids,
                )
                rows = [
                    {
                        **row,
                        "retrieval_source": "recent_active",
                        "selection_reason": ["recent_active", "active_memory"],
                        "provider": provider,
                        "embedding_model": None,
                        "fallback_chain": ["recent_active"],
                        "degraded_reason": degraded_reason,
                    }
                    for row in rows
                    if _memory_row_allowed(
                        row,
                        organization_id=organization_id,
                        member_id=member_id,
                        request=request,
                    )
                ]
                retrieval_sources.add("recent_active")
            if rows and "semantic_vector" not in retrieval_sources and self._vector is not None:
                degraded_reason = "vector_hits_insufficient_fts_fallback"
                rows = [
                    {
                        **row,
                        "degraded_reason": row.get("degraded_reason") or degraded_reason,
                    }
                    for row in rows
                ]
            retrieval_id = new_id("retr")
            rerank = self._reranker.rerank(
                rows,
                request=request,
                member_id=member_id,
                limit=request.limit,
            )
            rows = rerank.rows
            selected_ids = [row["memory_id"] for row in rows]
            diagnostics = await self._retrieval_diagnostics(
                organization_id=organization_id,
                member_id=member_id,
                selected_rows=rows,
                request=request,
                include_sensitive=request.include_sensitive,
                include_asset_scoped=request.include_asset_scoped,
                asset_scope_ids=request.asset_scope_ids,
            )
            suppressed = [
                *rerank.suppressed,
                *[
                    _suppressed_item(
                        target_id=item.memory_id,
                        reason=item.reason,
                        sensitivity=None,
                        score=0.0,
                        metadata={"source": "memory_retrieval_diagnostics"},
                    )
                    for item in diagnostics.filtered
                ],
            ]
            now = utc_now_iso()
            await self._repo.touch_accessed(selected_ids, now)
            await self._repo.insert_retrieval_log(
                retrieval_id=retrieval_id,
                organization_id=organization_id,
                trace_id=trace_id,
                turn_id=turn_id,
                conversation_id=request.conversation_id,
                member_id=member_id,
                query_text_hash=_hash_text(str(redact(request.query))),
                intent=request.intent,
                selected_memory_ids=selected_ids,
                filtered_memory_ids=[item.memory_id for item in diagnostics.filtered],
                ranking=[item.model_dump(mode="json") for item in diagnostics.ranking],
                token_budget={
                    "limit": request.limit,
                    "provider": provider,
                    "retrieval_sources": sorted(retrieval_sources),
                    "fallback_policy": "fts",
                },
                recall_scope_applied=_recall_scope_applied(request),
                request_filters={
                    "recall_scope": request.recall_scope,
                    "include_cross_session": request.include_cross_session,
                    "exclude_conversation_id": request.exclude_conversation_id,
                    "memory_classes": list(request.memory_classes or []),
                    "durability_filter": list(request.durability_filter or []),
                    "freshness_policy": request.freshness_policy,
                },
                degraded=degraded_reason is not None and "semantic_vector" not in retrieval_sources,
                created_at=now,
            )
            await self._persist_retrieval_quality(
                retrieval_id=retrieval_id,
                organization_id=organization_id,
                target_type="memory",
                provider=provider,
                input_count=len(rows) + len(rerank.suppressed),
                selected_count=len(rows),
                suppressed=suppressed,
                fallback_used="semantic_vector" not in retrieval_sources,
                latency_ms=rerank.latency_ms,
                trace_id=trace_id,
                created_at=now,
            )
            await self._end_span(
                span_id,
                output_data={
                    "retrieval_id": retrieval_id,
                    "selected_count": len(rows),
                    "selected_memory_ids": selected_ids,
                    "filtered_count": len(diagnostics.filtered),
                    "suppressed_count": len(suppressed),
                    "retrieval_sources": sorted(retrieval_sources),
                    "degraded_reason": degraded_reason,
                },
            )
            return MemorySearchApiResponse(
                retrieval_id=retrieval_id,
                memory_contract_version=MEMORY_SEMANTIC_CONTRACT_VERSION,
                degraded=degraded_reason is not None and "semantic_vector" not in retrieval_sources,
                recall_scope_applied=_recall_scope_applied(request),
                provider=provider,
                degraded_reason=degraded_reason,
                selected_memory_ids=selected_ids,
                filtered=diagnostics.filtered,
                ranking=diagnostics.ranking,
                items=[
                    MemorySearchHit(
                        memory_id=row["memory_id"],
                        layer=MemoryLayer(row["layer"]),
                        kind=row["kind"],
                        memory_class=_memory_class_for_row(row),
                        summary_text=row["summary_text"],
                        score=float(row.get("rank_score", row.get("importance", 0.0))),
                        confidence=float(row["confidence"]),
                        importance=float(row["importance"]),
                        sensitivity=row["sensitivity"],
                        validity=_memory_validity(row),
                        scope_policy=_scope_policy_for_row(row),
                        durability=_durability_for_row(row),
                        freshness_state=_freshness_state_for_row(row),
                        cross_session=_is_cross_session_memory(
                            row,
                            conversation_id=request.conversation_id,
                        ),
                        embedding_status=row.get("embedding_status", "pending"),
                        quality_score=float(row.get("quality_score", 0.5) or 0.5),
                        quality_breakdown=row.get("quality_breakdown", {}),
                        version_index=int(row.get("version_index") or 1),
                        conflict_group_id=row.get("conflict_group_id"),
                        conflict_status=row.get("conflict_status", "clear"),
                        reuse_score=float(row.get("reuse_score", 0.0) or 0.0),
                        reuse_count=int(row.get("reuse_count") or 0),
                        retrieval_source=row.get("retrieval_source", "fts_fallback"),
                        selection_reason=row.get("selection_reason", []),
                        provider=row.get("provider"),
                        embedding_model=row.get("embedding_model"),
                        fallback_chain=row.get("fallback_chain", []),
                        degraded_reason=row.get("degraded_reason"),
                        rerank_score=row.get("rerank_score"),
                        selection_confidence=row.get("selection_confidence"),
                        conflict_notes=row.get("conflict_notes", []),
                        suppressed_reason=row.get("suppressed_reason"),
                        suppressed_reason_codes=_suppressed_reason_codes(row),
                        supersedes=row.get("supersedes"),
                        superseded_by=_superseded_by(row),
                        correction_status=_correction_status(row),
                        evidence_strength=_evidence_strength_for_row(row),
                        requires_user_confirmation=bool(
                            row.get("requires_user_confirmation", False)
                        ),
                        source=_public_memory_source(row["source"]),
                    )
                    for row in rows
                ],
            )
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.MEMORY_SEARCH_FAILED.value},
                error_code=ErrorCode.MEMORY_SEARCH_FAILED.value,
            )
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.MEMORY_SEARCH_FAILED,
                "记忆检索失败",
                status_code=500,
            ) from exc

    async def compress(
        self,
        search_response: MemorySearchApiResponse,
        *,
        token_budget: int = 1200,
        trace_id: str | None = None,
    ) -> list[MemoryBlock]:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_COMPRESS,
            "compress memory blocks",
            metadata={"retrieval_id": search_response.retrieval_id},
        )
        groups: dict[str, list[MemorySearchHit]] = {}
        for item in search_response.items:
            group_key = f"{item.memory_class}:{item.freshness_state}"
            groups.setdefault(group_key, []).append(item)
        blocks: list[MemoryBlock] = []
        token_total = 0
        for group_key, items in groups.items():
            memory_class, freshness_state = group_key.split(":", 1)
            title = _memory_block_title(memory_class, freshness_state=freshness_state)
            block_items: list[MemoryBlockItem] = []
            ordered_items = sorted(
                items,
                key=lambda item: (
                    item.freshness_state != "fresh",
                    -float(item.evidence_strength),
                    -float(item.selection_confidence or 0.0),
                    -float(item.quality_score),
                ),
            )
            for item in ordered_items:
                item_tokens = estimate_text_tokens(item.summary_text)
                if token_total + item_tokens > token_budget and block_items:
                    break
                token_total += item_tokens
                block_items.append(
                    MemoryBlockItem(
                        memory_id=item.memory_id,
                        kind=item.kind,
                        summary=item.summary_text,
                        confidence=item.confidence,
                        source_ref={
                            **item.source.model_dump(mode="json"),
                            "selection_reason": item.selection_reason,
                            "retrieval_source": item.retrieval_source,
                            "sensitivity": item.sensitivity,
                            "validity": item.validity,
                            "selection_confidence": item.selection_confidence,
                            "quality_score": item.quality_score,
                            "version_index": item.version_index,
                            "reuse_score": item.reuse_score,
                            "conflict_status": item.conflict_status,
                            "memory_class": item.memory_class,
                            "scope_policy": item.scope_policy,
                            "durability": item.durability,
                            "freshness_state": item.freshness_state,
                            "cross_session": item.cross_session,
                            "evidence_strength": item.evidence_strength,
                        },
                    )
                )
            if block_items:
                selection_reason = sorted(
                    {
                        reason
                        for item in items
                        for reason in (item.selection_reason or [item.retrieval_source])
                    }
                )
                blocks.append(
                    MemoryBlock(
                        block_id=new_id("memblk"),
                        block_type=_block_type_for_kind(memory_class),
                        title=title,
                        items=block_items,
                        token_estimate=sum(
                            estimate_text_tokens(item.summary)
                            for item in block_items
                        ),
                        selection_reason=selection_reason or ["active_memory"],
                    )
                )
        await self._end_span(
            span_id,
            output_data={
                "retrieval_id": search_response.retrieval_id,
                "block_count": len(blocks),
                "token_estimate": token_total,
            },
        )
        return blocks

    async def _recent_active_fallback(
        self,
        *,
        organization_id: str,
        member_id: str,
        limit: int,
        memory_classes: list[str],
        durability_filter: list[str],
        include_sensitive: bool,
        include_asset_scoped: bool,
        asset_scope_ids: list[str],
    ) -> list[dict[str, Any]]:
        rows = await self._repo.list_context_candidates(
            organization_id=organization_id,
            member_id=member_id,
            limit=50,
            include_asset_scoped=include_asset_scoped,
            asset_scope_ids=asset_scope_ids,
        )
        return [
            {
                **row,
                "rank_score": float(row.get("importance", 0.0)) * float(row.get("confidence", 0.0)),
            }
            for row in rows
            if row["status"] == "active"
            and (not memory_classes or _memory_class_for_row(row) in memory_classes)
            and (not durability_filter or _durability_for_row(row) in durability_filter)
            and (
                include_sensitive
                or row["sensitivity"] not in {"high", "secret", "credential", "wallet"}
            )
        ][:limit]

    async def _retrieval_diagnostics(
        self,
        *,
        organization_id: str,
        member_id: str,
        selected_rows: list[dict[str, Any]],
        request: MemorySearchApiRequest,
        include_sensitive: bool,
        include_asset_scoped: bool,
        asset_scope_ids: list[str],
    ) -> MemoryRetrievalDiagnostics:
        selected_ids = {row["memory_id"] for row in selected_rows}
        candidates = await self._repo.list_context_candidates(
            organization_id=organization_id,
            member_id=member_id,
            limit=200,
            include_asset_scoped=True,
            asset_scope_ids=["*"],
        )
        filtered: list[MemorySearchFilteredItem] = []
        for item in candidates:
            memory_id = str(item["memory_id"])
            if memory_id in selected_ids:
                continue
            reason = _filter_reason(
                item,
                request=request,
                include_sensitive=include_sensitive,
                include_asset_scoped=include_asset_scoped,
                asset_scope_ids=asset_scope_ids,
            )
            if reason:
                filtered.append(
                    MemorySearchFilteredItem(
                        memory_id=memory_id,
                        reason=reason,
                        status=str(item.get("status") or "active"),
                        freshness_state=_freshness_state_for_row(item),
                        memory_class=_memory_class_for_row(item),
                        durability=_durability_for_row(item),
                        supersedes=item.get("supersedes"),
                        superseded_by=_superseded_by(item),
                        correction_status=_correction_status(item),
                    )
                )
        ranking = [
            MemorySearchRankingItem(
                memory_id=row["memory_id"],
                score=float(
                    row.get(
                        "rerank_score",
                        row.get("rank_score", row.get("importance", 0.0)),
                    )
                ),
                reason_codes=row.get("selection_reason")
                or [row.get("retrieval_source") or "recent_active"],
            )
            for row in selected_rows
        ]
        return MemoryRetrievalDiagnostics(
            selected_memory_ids=list(selected_ids),
            filtered=filtered,
            ranking=ranking,
        )

    async def _persist_retrieval_quality(
        self,
        *,
        retrieval_id: str,
        organization_id: str,
        target_type: str,
        provider: str | None,
        input_count: int,
        selected_count: int,
        suppressed: list[dict[str, Any]],
        fallback_used: bool,
        latency_ms: float,
        trace_id: str | None,
        created_at: str,
    ) -> None:
        if self._retrieval_repo is None:
            return
        await self._retrieval_repo.insert_rerank_run(
            {
                "rerank_run_id": new_id("rrank"),
                "retrieval_id": retrieval_id,
                "organization_id": organization_id,
                "target_type": target_type,
                "provider": provider,
                "scoring_policy": self._reranker.scoring_policy,
                "input_count": input_count,
                "selected_count": selected_count,
                "suppressed_count": len(suppressed),
                "fallback_used": fallback_used,
                "latency_ms": latency_ms,
                "trace_id": trace_id,
                "created_at": created_at,
            }
        )
        for item in suppressed:
            await self._retrieval_repo.insert_suppressed_item(
                {
                    "suppressed_id": new_id("rsup"),
                    "retrieval_id": retrieval_id,
                    "organization_id": organization_id,
                    "target_type": target_type,
                    "target_id": item["target_id"],
                    "reason": item["reason"],
                    "sensitivity": item.get("sensitivity"),
                    "selection_score": item.get("selection_score", 0.0),
                    "metadata": item.get("metadata", {}),
                    "trace_id": trace_id,
                    "created_at": created_at,
                }
            )
        await self._retrieval_repo.insert_quality_report(
            {
                "report_id": new_id("rqr"),
                "organization_id": organization_id,
                "target_type": target_type,
                "retrieval_id": retrieval_id,
                "summary": {
                    "selected_count": selected_count,
                    "suppressed_count": len(suppressed),
                    "fallback_used": fallback_used,
                },
                "metrics": {
                    "latency_ms": round(latency_ms, 4),
                    "input_count": input_count,
                    "precision_smoke": 1.0 if selected_count else 0.0,
                },
                "status": "completed",
                "trace_id": trace_id,
                "created_at": created_at,
            }
        )

    async def _default_conversation_id_for_member(self, member_id: str) -> str | None:
        conversations = await self._chat.list_conversations()
        for conversation in conversations:
            if conversation.get("primary_member_id") == member_id:
                return str(conversation["conversation_id"])
        return None

    async def _memory_source_payload(
        self,
        *,
        source_type: str,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None = None,
        message_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        tool_call_id: str | None = None,
        approval_id: str | None = None,
        trace_id: str | None = None,
        channel: str | None = None,
        channel_event_id: str | None = None,
        artifact_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical_type = _canonical_memory_source_type(source_type, conversation_id=conversation_id)
        resolved_conversation_id = conversation_id
        if resolved_conversation_id is None:
            resolved_conversation_id = await self._default_conversation_id_for_member(member_id)
        payload = {
            "type": canonical_type,
            "conversation_id": resolved_conversation_id,
            "turn_id": turn_id,
            "task_id": task_id,
            "step_id": step_id,
            "message_id": message_id,
            "tool_call_id": tool_call_id,
            "approval_id": approval_id,
            "trace_id": trace_id,
            "channel": channel or ("local" if canonical_type != "external_ingest" else None),
            "captured_at": utc_now_iso(),
            "channel_event_id": channel_event_id,
            "artifact_id": artifact_id,
        }
        if extra:
            payload.update(extra)
        return payload

    async def _record_memory_ledger(
        self,
        *,
        source: dict[str, Any],
        decision: str,
        summary: str,
        candidate_id: str | None = None,
        memory_id: str | None = None,
    ) -> None:
        if self._chat_run_ledger is None:
            return
        await self._chat_run_ledger.record_memory_write_decision(
            turn_id=source.get("turn_id"),
            trace_id=source.get("trace_id"),
            conversation_id=source.get("conversation_id"),
            memory_id=memory_id,
            candidate_id=candidate_id,
            decision=decision,
            source=source,
            summary=summary,
        )

    async def extract_from_turn(
        self,
        turn_id: str,
        *,
        trace_id: str | None = None,
        root_span_id: str | None = None,
    ) -> MemoryExtractResponse:
        turn = await self._chat.get_turn(turn_id)
        if turn is None:
            raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
        user_message = await self._chat.get_message(turn["user_message_id"])
        text = str(user_message["content_text"] if user_message else "")
        return await self.extract_from_text(
            text,
            member_id=turn["member_id"],
            conversation_id=turn["conversation_id"],
            turn_id=turn_id,
            message_id=turn["user_message_id"],
            trace_id=trace_id or turn["trace_id"],
            root_span_id=root_span_id,
            allow_implicit=True,
        )

    async def extract_from_text(
        self,
        text: str,
        *,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None = None,
        message_id: str | None = None,
        trace_id: str | None = None,
        root_span_id: str | None = None,
        force: bool = False,
        allow_implicit: bool = False,
        create_job: bool = True,
    ) -> MemoryExtractResponse:
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        command = self._classify_command(text, force=force, allow_implicit=allow_implicit)
        if command is None:
            return MemoryExtractResponse(candidates=[], memories=[])
        source = await self._memory_source_payload(
            source_type="conversation_turn" if conversation_id else "external_ingest",
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            message_id=message_id,
            trace_id=trace_id,
            channel="local",
        )
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_EXTRACT,
            "extract memory candidates",
            parent_span_id=root_span_id,
            input_data={"text": text},
            metadata={"member_id": member_id, "command": command.kind},
        )
        try:
            if create_job:
                await self._repo.insert_job(
                    job_id=new_id("memjob"),
                    organization_id=member["organization_id"],
                    turn_id=turn_id,
                    idempotency_key=f"memory.extract:{turn_id or _hash_text(text)}",
                    job_type="memory_extract",
                    status="running",
                    payload={"member_id": member_id, "conversation_id": conversation_id},
                    created_at=utc_now_iso(),
                )
            result = await self._write_candidate_pipeline(
                command=command,
                text=text,
                organization_id=member["organization_id"],
                member_id=member_id,
                source=source,
                trace_id=trace_id,
                root_span_id=span_id,
            )
            if create_job:
                await self._repo.update_job_status(
                    idempotency_key=f"memory.extract:{turn_id or _hash_text(text)}",
                    status="completed",
                    error_code=None,
                    error_message=None,
                    updated_at=utc_now_iso(),
                    completed_at=utc_now_iso(),
                )
            await self._end_span(
                span_id,
                output_data={
                    "candidate_count": len(result.candidates),
                    "memory_count": len(result.memories),
                    "blocked": result.blocked,
                },
            )
            return MemoryExtractResponse(
                candidates=result.candidates,
                memories=result.memories,
                blocked=result.blocked,
                reason=result.reason,
            )
        except Exception as exc:
            if create_job:
                await self._repo.update_job_status(
                    idempotency_key=f"memory.extract:{turn_id or _hash_text(text)}",
                    status="failed",
                    error_code=ErrorCode.MEMORY_EXTRACT_FAILED.value,
                    error_message="记忆抽取失败",
                    updated_at=utc_now_iso(),
                )
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.MEMORY_EXTRACT_FAILED.value},
                error_code=ErrorCode.MEMORY_EXTRACT_FAILED.value,
            )
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.MEMORY_EXTRACT_FAILED,
                "记忆抽取失败",
                status_code=500,
            ) from exc

    async def record_multimodal_attachment(
        self,
        *,
        summary_text: str,
        organization_id: str,
        member_id: str,
        source: dict[str, Any],
        trace_id: str | None = None,
        root_span_id: str | None = None,
        status: str = "understood",
    ) -> MemoryCommandResult:
        clean_text = str(redact(summary_text)).strip()
        if not clean_text:
            return MemoryCommandResult(handled=False, reason="empty_summary")
        kind = _kind_for_summary(clean_text)
        command = MemoryCommand(
            kind="multimodal_attachment_fact",
            memory_kind=kind,
            layer=(
                MemoryLayer.PROCEDURAL.value
                if kind == "skill_candidate"
                else MemoryLayer.SEMANTIC.value
            ),
            summary=clean_text,
            score=0.68 if status == "understood" else 0.58,
            explicit=False,
        )
        return await self._write_candidate_pipeline(
            command=command,
            text=clean_text,
            organization_id=organization_id,
            member_id=member_id,
            source=source,
            trace_id=trace_id,
            root_span_id=root_span_id,
        )

    async def enqueue_extract_after_turn(self, turn_id: str, *, schedule: bool = False) -> None:
        turn = await self._chat.get_turn(turn_id)
        if turn is None or turn["status"] != "completed":
            return
        user_message = await self._chat.get_message(turn["user_message_id"])
        text = str(user_message["content_text"] if user_message else "")
        if _is_explicit_memory_command(text):
            return
        now = utc_now_iso()
        await self._repo.insert_job(
            job_id=new_id("memjob"),
            organization_id=await self._organization_id_for_member(turn["member_id"]),
            turn_id=turn_id,
            idempotency_key=f"memory.extract_after_turn:{turn_id}",
            job_type="extract_after_turn",
            status="pending",
            payload={
                "member_id": turn["member_id"],
                "conversation_id": turn["conversation_id"],
                "user_message_id": turn["user_message_id"],
                "trace_id": turn["trace_id"],
            },
            created_at=now,
        )
        if schedule:
            self._schedule_background_jobs()

    async def record_recovery_lesson_candidate(
        self,
        *,
        turn_id: str,
        stage: str,
        failure_type: str,
        recovery_action: str,
        trace_id: str | None,
    ) -> MemoryCandidate | None:
        turn = await self._chat.get_turn(turn_id)
        if turn is None:
            return None
        organization_id = await self._organization_id_for_member(turn["member_id"])
        summary = (
            f"聊天恢复经验：{stage} 阶段的 {failure_type} 可尝试 {recovery_action}，"
            "但不得绕过 Safety、Approval、Capability Graph 或 Asset Broker。"
        )
        now = utc_now_iso()
        source = await self._memory_source_payload(
            source_type="task_result",
            member_id=turn["member_id"],
            conversation_id=turn["conversation_id"],
            turn_id=turn_id,
            message_id=turn.get("user_message_id"),
            trace_id=trace_id,
            channel="local",
        )
        return await self._insert_candidate(
            organization_id=organization_id,
            member_id=turn["member_id"],
            source=source,
            proposed_layer=MemoryLayer.PROCEDURAL.value,
            proposed_kind="recovery_lesson",
            proposed_scope_type="member",
            proposed_scope_id=turn["member_id"],
            summary_text=str(redact(summary)),
            payload={
                "stage": stage,
                "failure_type": failure_type,
                "recovery_action": recovery_action,
                "controls": ["safety", "approval", "capability_graph", "asset_broker"],
            },
            score={"source": "turn_recovery", "review_required": True},
            final_score=0.62,
            sensitivity="low",
            decision="pending",
            decision_reason="recovery_lesson_review",
            now=now,
        )

    async def recover_stale_jobs(self) -> int:
        stale_before = (utc_now() - timedelta(minutes=JOB_STALE_AFTER_MINUTES)).isoformat()
        return await self._repo.restore_stale_jobs(
            stale_before=stale_before,
            updated_at=utc_now_iso(),
        )

    async def process_pending_jobs(self, *, limit: int = 10) -> int:
        processed = 0
        for _ in range(limit):
            job = await self._repo.claim_next_job(
                worker_id=WORKER_ID,
                now=utc_now_iso(),
            )
            if job is None:
                break
            await self._execute_job(job)
            processed += 1
        return processed

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
    ) -> list[MemoryJobItem]:
        rows = await self._repo.list_jobs(status=status, job_type=job_type, limit=limit)
        return [
            MemoryJobItem(
                job_id=row["job_id"],
                organization_id=row["organization_id"],
                turn_id=row.get("turn_id"),
                idempotency_key=row["idempotency_key"],
                job_type=row["job_type"],
                status=row["status"],
                attempts=int(row.get("attempts") or 0),
                max_attempts=int(row.get("max_attempts") or 3),
                next_run_at=row.get("next_run_at"),
                locked_by=row.get("locked_by"),
                locked_at=row.get("locked_at"),
                error_code=row.get("error_code"),
                error_message=(
                    str(redact(row["error_message"])) if row.get("error_message") else None
                ),
                payload=redact(row.get("payload", {})),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row.get("completed_at"),
            )
            for row in rows
        ]

    def _schedule_background_jobs(self) -> None:
        task = asyncio.create_task(self.process_pending_jobs())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _execute_job(self, job: dict[str, Any]) -> None:
        now = utc_now_iso()
        try:
            if job["job_type"] != "extract_after_turn":
                await self._repo.update_job_status(
                    idempotency_key=job["idempotency_key"],
                    status="failed",
                    error_code=ErrorCode.MEMORY_EXTRACT_FAILED.value,
                    error_message=f"unsupported memory job type: {job['job_type']}",
                    updated_at=now,
                )
                return
            payload = job["payload"]
            turn_id = str(job["turn_id"] or "")
            user_message = await self._chat.get_message(str(payload.get("user_message_id") or ""))
            if user_message is None:
                raise AppError(ErrorCode.NOT_FOUND, "记忆 job 的来源消息不存在", status_code=404)
            await self.extract_from_text(
                str(user_message.get("content_text") or ""),
                member_id=str(payload["member_id"]),
                conversation_id=payload.get("conversation_id"),
                turn_id=turn_id,
                message_id=user_message["message_id"],
                trace_id=payload.get("trace_id"),
                allow_implicit=True,
                create_job=False,
            )
            await self._repo.update_job_status(
                idempotency_key=job["idempotency_key"],
                status="completed",
                error_code=None,
                error_message=None,
                updated_at=utc_now_iso(),
                completed_at=utc_now_iso(),
            )
        except Exception as exc:
            attempts = int(job.get("attempts") or 0)
            max_attempts = int(job.get("max_attempts") or 3)
            terminal = attempts >= max_attempts
            await self._repo.update_job_status(
                idempotency_key=job["idempotency_key"],
                status="failed" if terminal else "pending",
                error_code=ErrorCode.MEMORY_EXTRACT_FAILED.value,
                error_message=str(redact(str(exc))),
                updated_at=utc_now_iso(),
            )

    async def _organization_id_for_member(self, member_id: str) -> str:
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        return str(member["organization_id"])

    async def handle_explicit_chat_command(
        self,
        *,
        text: str,
        member_id: str,
        conversation_id: str,
        turn_id: str,
        message_id: str,
        trace_id: str,
        root_span_id: str | None,
    ) -> MemoryCommandResult:
        if _is_explicit_forget_command(text):
            return MemoryCommandResult(
                handled=True,
                response_text=(
                    "我不能在聊天里假装已经删除长期记忆，因为删除需要明确权限和操作记录。"
                    "我现在能做的是：先把这批临时测试偏好停用，后续不再主动沿用它；"
                    "如果要真正删除，还需要通过记忆管理功能明确删除范围、来源和操作记录。"
                    "在那之前，我只会如实说明边界，不会把“已经忘记”说成既成事实。"
                ),
                reason="forget_requires_memory_management_boundary",
            )
        if not _is_explicit_memory_command(text):
            return MemoryCommandResult(handled=False)
        command = self._classify_command(text)
        if command is None:
            return MemoryCommandResult(handled=False)
        result = await self.extract_from_text(
            text,
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            message_id=message_id,
            trace_id=trace_id,
            root_span_id=root_span_id,
            create_job=False,
        )
        if result.blocked:
            response = (
                "这条内容涉及敏感信息，我不会把它写入长期记忆。"
                "如果你只是想让我记住处理方式，请用占位符描述，不要贴真实 token、密码或私钥。"
            )
        elif command.kind == "block":
            response = "好的，这条不会写入长期记忆；后续我也不会把它当作长期偏好、长期规则或事实主动使用。"
        elif command.kind == "correction" and result.memories:
            if any(memory.supersedes for memory in result.memories):
                response = (
                    f"这条长期记忆已纠正：{_memory_summary_for_reply(result.memories)}\n"
                    "后面我会按这条新的长期记忆回答；如果你在当前对话里再改口，我会以最新要求为准。"
                )
            else:
                response = (
                    f"这条长期记忆纠正已记录：{_memory_summary_for_reply(result.memories)}\n"
                    "暂时没有找到可精确替代的旧记忆，所以我先把它作为新的长期记忆记录下来。"
                )
        elif result.memories:
            response = (
                f"我已经记住这条长期记忆：{_memory_summary_for_reply(result.memories)}\n"
                "后面同一批聊天里，我会优先按这条偏好、规则或事实组织回复；如果你临时改口，我会以新的要求为准。"
            )
        elif result.candidates and result.candidates[0].decision == "discarded_duplicate":
            response = (
                "这条长期记忆我已经记过了，不会重复写入。"
                "后续仍会按已有记忆使用；如果你要改写它，可以直接说“纠正记忆：...”。"
            )
        else:
            response = (
                "我没有把这条写入长期记忆。"
                "通常是因为它更像临时对话内容，或者还不够稳定，暂时不适合进入长期记忆。"
            )
        return MemoryCommandResult(
            handled=True,
            response_text=response,
            candidates=result.candidates,
            memories=result.memories,
            blocked=result.blocked,
            reason=result.reason,
        )

    async def handle_memory_query(
        self,
        *,
        text: str,
        member_id: str,
        conversation_id: str,
        trace_id: str | None,
        turn_id: str | None = None,
    ) -> str | None:
        preference_only = explicit_preference_recall_query(text)
        search_response = await self.search(
            MemorySearchApiRequest(
                query=text,
                member_id=member_id,
                conversation_id=conversation_id,
                intent="memory_query",
                limit=5,
                memory_classes=["preference"] if preference_only else [],
                include_sensitive=False,
                include_cross_session=True,
            ),
            default_member_id=member_id,
            trace_id=trace_id,
            turn_id=turn_id,
        )
        summaries: list[str] = []
        for item in search_response.items[:3]:
            if item.sensitivity in {"high", "secret", "credential", "wallet"}:
                continue
            if preference_only and item.memory_class != "preference":
                continue
            summary = str(item.summary_text or "").strip()
            if summary:
                summaries.append(summary)
        if not summaries:
            return (
                "\u6211\u8fd9\u91cc\u6ca1\u6709\u627e\u5230\u53ef\u4ee5\u53ec\u56de\u7684\u957f\u671f\u8bb0\u5fc6\u3002"
                "\u5982\u679c\u4f60\u662f\u60f3\u8ba9\u6211\u73b0\u5728\u8bb0\u4f4f\uff0c\u53ef\u4ee5\u76f4\u63a5\u8bf4\u201c\u8bb0\u4f4f ...\u201d\u3002"
            )
        if len(summaries) == 1:
            return summaries[0]
        return "\n".join(f"{idx}. {summary}" for idx, summary in enumerate(summaries, start=1))

    async def approve_candidate(
        self,
        candidate_id: str,
        *,
        trace_id: str | None = None,
    ) -> tuple[MemoryCandidate, MemoryItem | None]:
        candidate = await self._repo.get_candidate(candidate_id)
        if candidate is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆候选不存在", status_code=404)
        if candidate["decision"] in {"auto_written", "user_approved"}:
            return _memory_candidate(candidate), None
        if candidate["decision"] == "user_rejected":
            raise AppError(
                ErrorCode.CONFLICT,
                "已拒绝的记忆候选不能再次批准",
                status_code=409,
            )
        if candidate["sensitivity"] in {"high", "secret"}:
            raise AppError(
                ErrorCode.MEMORY_POLICY_BLOCKED,
                "敏感候选不能批准为长期记忆",
                status_code=400,
            )
        now = utc_now_iso()
        candidate = await self._before_memory_write_candidate(candidate, trace_id=trace_id)
        memory = await self._insert_memory_from_candidate(
            candidate,
            decision="user_approved",
            trace_id=trace_id,
            now=now,
        )
        await self._audit.write_event(
            actor_type="system",
            action="memory.candidate.approved",
            object_type="memory_candidate",
            object_id=candidate_id,
            summary="记忆候选已批准",
            risk_level=RiskLevel.R1,
            payload={"candidate_id": candidate_id, "memory_id": memory.memory_id},
            trace_id=trace_id,
        )
        return _memory_candidate(await self._repo.get_candidate(candidate_id) or candidate), memory

    async def reject_candidate(
        self,
        candidate_id: str,
        *,
        trace_id: str | None = None,
    ) -> MemoryCandidate:
        candidate = await self._repo.get_candidate(candidate_id)
        if candidate is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆候选不存在", status_code=404)
        now = utc_now_iso()
        await self._repo.update_candidate_decision(
            candidate_id,
            decision="user_rejected",
            decision_reason="user_rejected",
            decided_at=now,
            updated_at=now,
        )
        await self._audit.write_event(
            actor_type="system",
            action="memory.candidate.rejected",
            object_type="memory_candidate",
            object_id=candidate_id,
            summary="记忆候选已拒绝",
            risk_level=RiskLevel.R1,
            payload={"candidate_id": candidate_id},
            trace_id=trace_id,
        )
        return _memory_candidate(await self._repo.get_candidate(candidate_id) or candidate)

    async def archive_memory(self, memory_id: str, *, trace_id: str | None = None) -> MemoryItem:
        return await self._set_memory_status(
            memory_id,
            status="archived",
            trace_type=TraceSpanType.MEMORY_ARCHIVE,
            audit_action="memory.archived",
            audit_summary="记忆已归档",
            trace_id=trace_id,
        )

    async def restore_memory(self, memory_id: str, *, trace_id: str | None = None) -> MemoryItem:
        return await self._set_memory_status(
            memory_id,
            status="active",
            trace_type=TraceSpanType.MEMORY_ARCHIVE,
            audit_action="memory.restored",
            audit_summary="记忆已恢复",
            trace_id=trace_id,
        )

    async def delete_memory(self, memory_id: str, *, trace_id: str | None = None) -> MemoryItem:
        return await self._set_memory_status(
            memory_id,
            status="deleted",
            trace_type=TraceSpanType.MEMORY_DELETE,
            audit_action="memory.deleted",
            audit_summary="记忆已删除",
            trace_id=trace_id,
        )

    async def list_candidates(
        self,
        *,
        member_id: str | None = None,
        decision: str | None = None,
        limit: int = 50,
    ) -> list[MemoryCandidate]:
        rows = await self._repo.list_candidates(
            member_id=member_id,
            decision=decision,
            limit=limit,
        )
        return [_memory_candidate(row) for row in rows]

    async def list_relations(self, memory_id: str) -> list[dict[str, Any]]:
        if await self._repo.get_memory_item(memory_id) is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在", status_code=404)
        return await self._repo.list_relations(memory_id)

    async def list_experience_records(
        self,
        *,
        member_id: str | None = None,
        task_id: str | None = None,
        outcome: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[MemoryExperienceRecord]:
        rows = await self._repo.list_experience_records(
            member_id=member_id,
            task_id=task_id,
            outcome=outcome,
            status=status,
            limit=limit,
        )
        return [_experience_record(row) for row in rows]

    async def list_conflicts(
        self,
        *,
        member_id: str | None = None,
        status: str | None = None,
        conflict_group_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryConflictRecord]:
        rows = await self._repo.list_conflict_records(
            member_id=member_id,
            status=status,
            conflict_group_id=conflict_group_id,
            limit=limit,
        )
        return [_conflict_record(row) for row in rows]

    async def list_reuse_feedback(
        self,
        *,
        retrieval_id: str | None = None,
        memory_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryReuseFeedback]:
        rows = await self._repo.list_reuse_feedback(
            retrieval_id=retrieval_id,
            memory_id=memory_id,
            limit=limit,
        )
        return [_reuse_feedback(row) for row in rows]

    async def source_for_memory(self, memory_id: str) -> dict[str, Any]:
        memory = await self._repo.get_memory_item(memory_id)
        if memory is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在", status_code=404)
        source = memory["source"]
        source_message = None
        message_id = source.get("message_id")
        if message_id:
            source_message = await self._chat.get_message(str(message_id))
        return {
            "memory_id": memory_id,
            "source": source,
            "source_message": redact(source_message),
            "trace_id": source.get("trace_id"),
        }

    async def consolidate_experience(
        self,
        *,
        member_id: str,
        outcome: str,
        summary_text: str,
        source: dict[str, Any],
        task_id: str | None = None,
        conversation_id: str | None = None,
        evidence: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        trace_id: str | None = None,
        root_span_id: str | None = None,
    ) -> MemoryExperienceConsolidateResponse:
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        now = utc_now_iso()
        clean_summary = str(redact(summary_text)).strip()
        if not clean_summary:
            raise AppError(ErrorCode.VALIDATION_ERROR, "经验摘要不能为空", status_code=422)
        source_payload = await self._memory_source_payload(
            source_type=str(source.get("type") or "task_result"),
            member_id=member_id,
            conversation_id=conversation_id or source.get("conversation_id"),
            turn_id=source.get("turn_id"),
            message_id=source.get("message_id"),
            task_id=task_id or source.get("task_id"),
            step_id=source.get("step_id"),
            tool_call_id=source.get("tool_call_id"),
            approval_id=source.get("approval_id"),
            trace_id=trace_id or source.get("trace_id"),
            channel=source.get("channel") or "local",
            channel_event_id=source.get("channel_event_id"),
            artifact_id=source.get("artifact_id"),
        )
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_WRITE,
            "consolidate memory experience",
            parent_span_id=root_span_id,
            input_data={"task_id": task_id, "outcome": outcome},
            metadata={"member_id": member_id},
        )
        quality = _experience_quality_breakdown(
            summary_text=clean_summary,
            outcome=outcome,
            evidence=evidence or {},
            steps=steps or [],
            source=source_payload,
        )
        score = round(_experience_quality_score(quality), 4)
        conflict_group_id = str(
            source_payload.get("task_id")
            or source_payload.get("conversation_id")
            or new_id("memgrp")
        )
        base_kind = _experience_kind_for_outcome(outcome, steps or [])
        layer = _experience_layer_for_outcome(outcome, steps or [])
        decision = "auto_written" if score >= MIN_WRITE_SCORE and outcome != "failed" else "needs_review"
        candidate = await self._insert_candidate(
            organization_id=member["organization_id"],
            member_id=member_id,
            source=source_payload,
            proposed_layer=layer,
            proposed_kind=base_kind,
            proposed_scope_type="member",
            proposed_scope_id=member_id,
            summary_text=clean_summary,
            payload={
                "outcome": outcome,
                "task_id": task_id,
                "conversation_id": conversation_id,
                "steps": _redact_memory_evidence(steps or []),
                "evidence": _redact_memory_evidence(evidence or {}),
            },
            score={
                "quality_breakdown": quality,
                "quality_score": score,
                "experience": True,
                "outcome": outcome,
            },
            final_score=score,
            sensitivity=_experience_sensitivity(outcome, evidence or {}),
            decision=decision,
            decision_reason=None if decision == "auto_written" else "experience_requires_review",
            now=now,
        )
        await self._record_memory_ledger(
            source=source_payload,
            decision="candidate_recorded",
            summary=clean_summary,
            candidate_id=candidate.candidate_id,
        )
        conflicts = await self._resolve_experience_conflicts(
            organization_id=member["organization_id"],
            member_id=member_id,
            candidate=candidate,
            conflict_group_id=conflict_group_id,
            source=source_payload,
            outcome=outcome,
            evidence=evidence or {},
            trace_id=trace_id,
            now=now,
        )
        memories: list[MemoryItem] = []
        if decision == "auto_written":
            candidate_row = await self._before_memory_write_candidate(
                _candidate_row(candidate),
                trace_id=trace_id,
            )
            memory = await self._insert_memory_from_candidate(
                candidate_row,
                decision=decision,
                trace_id=trace_id,
                now=now,
                supersede_query=clean_summary,
                quality_score=score,
                quality_breakdown=quality,
                conflict_group_id=conflict_group_id,
                retention_policy=_retention_policy_for_experience(outcome, score),
            )
            memories.append(memory)
            await self._record_memory_ledger(
                source=source_payload,
                decision="written",
                summary=memory.summary_text,
                candidate_id=candidate.candidate_id,
                memory_id=memory.memory_id,
            )
        else:
            await self._record_memory_ledger(
                source=source_payload,
                decision=decision,
                summary=clean_summary,
                candidate_id=candidate.candidate_id,
            )
        experience = await self._persist_experience_record(
            organization_id=member["organization_id"],
            member_id=member_id,
            task_id=task_id,
            conversation_id=conversation_id,
            memory=memories[0] if memories else None,
            conflict_group_id=conflict_group_id,
            layer=layer,
            kind=base_kind,
            outcome=outcome,
            summary_text=clean_summary,
            source=source_payload,
            evidence=evidence or {},
            score={"quality_breakdown": quality, "quality_score": score},
            confidence_score=score,
            reuse_score=_experience_reuse_score(outcome, quality),
            decision=decision,
            trace_id=trace_id,
            now=now,
        )
        await self._end_span(
            span_id,
            output_data={
                "experience_id": experience.experience_id,
                "candidate_id": candidate.candidate_id,
                "memory_id": experience.memory_id,
                "conflict_count": len(conflicts),
                "decision": decision,
            },
        )
        return MemoryExperienceConsolidateResponse(
            experience=experience,
            candidates=[candidate],
            memories=memories,
            conflicts=conflicts,
        )

    async def record_retrieval_feedback(
        self,
        retrieval_id: str,
        request: MemoryReuseFeedbackRequest,
        *,
        trace_id: str | None = None,
    ) -> MemoryReuseFeedback:
        member_id = request.member_id or "mem_xiaoyao"
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        memory = await self._repo.get_memory_item(request.memory_id)
        if memory is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在", status_code=404)
        now = utc_now_iso()
        source = await self._memory_source_payload(
            source_type=str(request.source.get("type") or "external_ingest"),
            member_id=member_id,
            conversation_id=request.source.get("conversation_id"),
            turn_id=request.source.get("turn_id"),
            message_id=request.source.get("message_id"),
            task_id=request.task_id or request.source.get("task_id"),
            step_id=request.source.get("step_id"),
            tool_call_id=request.source.get("tool_call_id"),
            approval_id=request.source.get("approval_id"),
            trace_id=trace_id or request.trace_id or request.source.get("trace_id"),
            channel=request.source.get("channel"),
            channel_event_id=request.source.get("channel_event_id"),
            artifact_id=request.source.get("artifact_id"),
        )
        span_id = await self._start_span(
            trace_id or request.trace_id,
            TraceSpanType.MEMORY_CORRECTION,
            "record memory reuse feedback",
            metadata={
                "retrieval_id": retrieval_id,
                "memory_id": request.memory_id,
                "feedback_type": request.feedback_type,
            },
        )
        feedback = await self._persist_reuse_feedback(
            organization_id=member["organization_id"],
            member_id=member_id,
            retrieval_id=retrieval_id,
            memory_id=request.memory_id,
            task_id=request.task_id,
            feedback_type=request.feedback_type,
            rating=request.rating,
            source=source,
            evidence=request.evidence,
            trace_id=trace_id or request.trace_id,
            now=now,
        )
        reuse_delta = _reuse_feedback_delta(request.feedback_type, request.rating)
        await self._repo.update_memory_item(
            request.memory_id,
            {
                "reuse_score": max(
                    0.0,
                    min(1.0, float(memory.get("reuse_score", 0.0) or 0.0) + reuse_delta),
                ),
                "reuse_count": int(memory.get("reuse_count") or 0) + 1,
                "last_reused_at": now,
                "updated_at": now,
            },
        )
        await self._end_span(
            span_id,
            output_data={
                "feedback_id": feedback.feedback_id,
                "memory_id": request.memory_id,
                "rating": request.rating,
            },
        )
        return feedback

    async def _write_candidate_pipeline(
        self,
        *,
        command: MemoryCommand,
        text: str,
        organization_id: str,
        member_id: str,
        source: dict[str, Any],
        trace_id: str | None,
        root_span_id: str | None,
    ) -> MemoryCommandResult:
        source = _normalize_memory_source_dict(source)
        classification = self._safety.classify_chat_input(text)
        secret_hits = _sensitive_secret_hits(text)
        summary = command.summary
        now = utc_now_iso()
        if classification.sensitivity_hits or secret_hits:
            all_hits = [*classification.sensitivity_hits, *secret_hits]
            candidate = await self._insert_candidate(
                organization_id=organization_id,
                member_id=member_id,
                source=source,
                proposed_layer=command.layer,
                proposed_kind=command.memory_kind,
                proposed_scope_type="member",
                proposed_scope_id=member_id,
                summary_text=str(redact(summary)),
                payload={"fact": str(redact(summary))},
                score={"policy": "sensitive_block", "hits": all_hits},
                final_score=0.0,
                sensitivity="high",
                decision="discarded_sensitive",
                decision_reason="sensitive_content",
                now=now,
            )
            await self._audit.write_event(
                actor_type="system",
                action="memory.policy.blocked_sensitive",
                object_type="memory_candidate",
                object_id=candidate.candidate_id,
                summary="敏感内容未写入长期记忆",
                risk_level=RiskLevel.R2,
                payload={
                    "candidate_id": candidate.candidate_id,
                    "sensitivity_hits": all_hits,
                },
                trace_id=trace_id,
            )
            await self._record_memory_ledger(
                source=source,
                decision="discarded_sensitive",
                summary=str(redact(summary)),
                candidate_id=candidate.candidate_id,
            )
            return MemoryCommandResult(
                handled=True,
                candidates=[candidate],
                blocked=True,
                reason="sensitive_content",
            )

        if command.kind == "block":
            candidate = await self._insert_candidate(
                organization_id=organization_id,
                member_id=member_id,
                source=source,
                proposed_layer=MemoryLayer.SEMANTIC.value,
                proposed_kind="blocked_preference",
                proposed_scope_type="member",
                proposed_scope_id=member_id,
                summary_text=str(redact(summary or text)),
                payload={"fact": str(redact(summary or text))},
                score={"policy": "user_blocked"},
                final_score=0.0,
                sensitivity="low",
                decision="discarded_policy",
                decision_reason="user_said_do_not_remember",
                now=now,
            )
            await self._record_memory_ledger(
                source=source,
                decision="discarded_policy",
                summary=str(redact(summary or text)),
                candidate_id=candidate.candidate_id,
            )
            return MemoryCommandResult(handled=True, candidates=[candidate], reason="blocked")

        score_span = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_SCORE,
            "score memory candidate",
            parent_span_id=root_span_id,
            metadata={"kind": command.memory_kind},
        )
        quality_breakdown = _memory_quality_breakdown(
            summary_text=summary,
            kind=command.memory_kind,
            source=source,
            text=text,
            command_kind=command.kind,
        )
        quality_score = _memory_quality_score(quality_breakdown)
        final_score = max(float(command.score), quality_score)
        await self._end_span(
            score_span,
            output_data={
                "final_score": final_score,
                "quality_score": quality_score,
                "threshold": MIN_WRITE_SCORE,
            },
        )
        score = _score_decision(command, final_score=final_score)
        decision = score.decision
        normalized = _normalize(summary)
        dedupe_span = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_DEDUPE,
            "dedupe memory candidate",
            parent_span_id=root_span_id,
        )
        duplicate = await self._repo.find_duplicate(
            organization_id=organization_id,
            member_id=member_id,
            normalized_summary=normalized,
        )
        await self._end_span(
            dedupe_span,
            output_data={"duplicate_memory_id": duplicate["memory_id"] if duplicate else None},
        )
        if duplicate is not None and command.kind != "correction":
            candidate = await self._insert_candidate(
                organization_id=organization_id,
                member_id=member_id,
                source=source,
                proposed_layer=command.layer,
                proposed_kind=command.memory_kind,
                proposed_scope_type="member",
                proposed_scope_id=member_id,
                summary_text=summary,
                payload={"fact": summary},
                score={
                    "base": final_score,
                    "quality_breakdown": quality_breakdown,
                    "quality_score": final_score,
                    "duplicate_memory_id": duplicate["memory_id"],
                },
                final_score=final_score,
                sensitivity="low",
                decision="discarded_duplicate",
                decision_reason="duplicate_active_memory",
                now=now,
            )
            await self._record_memory_ledger(
                source=source,
                decision="discarded_duplicate",
                summary=summary,
                candidate_id=candidate.candidate_id,
            )
            return MemoryCommandResult(handled=True, candidates=[candidate], reason="duplicate")

        candidate = await self._insert_candidate(
            organization_id=organization_id,
            member_id=member_id,
            source=source,
            proposed_layer=command.layer,
            proposed_kind=command.memory_kind,
            proposed_scope_type="member",
            proposed_scope_id=member_id,
            summary_text=summary,
            payload={"fact": summary},
            score={
                "base": final_score,
                "explicit": command.explicit,
                "review_required": score.review_required,
                "quality_breakdown": quality_breakdown,
                "quality_score": final_score,
            },
            final_score=final_score,
            sensitivity="low",
            decision=decision,
            decision_reason=score.reason,
            now=now,
        )
        if decision != "auto_written":
            await self._record_memory_ledger(
                source=source,
                decision=decision,
                summary=summary,
                candidate_id=candidate.candidate_id,
            )
            return MemoryCommandResult(handled=True, candidates=[candidate], reason=decision)

        correction_span = None
        if command.kind == "correction":
            correction_span = await self._start_span(
                trace_id,
                TraceSpanType.MEMORY_CORRECTION,
                "apply explicit memory correction",
                parent_span_id=root_span_id,
                metadata={"candidate_id": candidate.candidate_id},
            )
        candidate_row = await self._before_memory_write_candidate(
            _candidate_row(candidate),
            trace_id=trace_id,
        )
        memory = await self._insert_memory_from_candidate(
            candidate_row,
            decision="auto_written",
            trace_id=trace_id,
            now=now,
            supersede_query=command.supersede_query,
        )
        if correction_span is not None:
            await self._end_span(
                correction_span,
                output_data={
                    "memory_id": memory.memory_id,
                    "supersedes": memory.supersedes,
                    "correction_status": "applied" if memory.supersedes else "not_found",
                },
            )
        await self._record_memory_ledger(
            source=source,
            decision="candidate_recorded",
            summary=summary,
            candidate_id=candidate.candidate_id,
        )
        await self._record_memory_ledger(
            source=source,
            decision="written",
            summary=memory.summary_text,
            candidate_id=candidate.candidate_id,
            memory_id=memory.memory_id,
        )
        return MemoryCommandResult(handled=True, candidates=[candidate], memories=[memory])

    async def _resolve_experience_conflicts(
        self,
        *,
        organization_id: str,
        member_id: str,
        candidate: MemoryCandidate,
        conflict_group_id: str,
        source: dict[str, Any],
        outcome: str,
        evidence: dict[str, Any],
        trace_id: str | None,
        now: str,
    ) -> list[MemoryConflictRecord]:
        matches = await self._repo.search_memory_items(
            organization_id=organization_id,
            member_id=member_id,
            query=candidate.summary_text,
            limit=3,
            include_archived=True,
            include_sensitive=False,
        )
        conflicts: list[MemoryConflictRecord] = []
        for match in matches:
            if match["status"] == "deleted":
                continue
            normalized_match = match.get("normalized_summary") or _normalize(match["summary_text"])
            normalized_candidate = _normalize(candidate.summary_text)
            if normalized_match == normalized_candidate:
                conflict_type = "duplicate_experience"
                status = "observed"
                resolution = "reuse_existing_memory"
            elif outcome in {"failed", "recovery_required"}:
                conflict_type = "failure_experience_overlap"
                status = "needs_review"
                resolution = None
            else:
                conflict_type = "related_experience"
                status = "observed"
                resolution = "rank_by_version_and_quality"
            conflicts.append(
                await self._persist_conflict_record(
                    organization_id=organization_id,
                    member_id=member_id,
                    memory_id=None,
                    related_memory_id=match["memory_id"],
                    candidate_id=candidate.candidate_id,
                    conflict_group_id=match.get("conflict_group_id") or conflict_group_id,
                    conflict_type=conflict_type,
                    status=status,
                    resolution=resolution,
                    summary_text=str(redact(candidate.summary_text)),
                    source=source,
                    evidence={
                        "outcome": outcome,
                        "matched_memory_id": match["memory_id"],
                        "matched_kind": match.get("kind"),
                        "matched_status": match.get("status"),
                        "experience_evidence": _redact_memory_evidence(evidence),
                    },
                    trace_id=trace_id,
                    now=now,
                )
            )
        return conflicts

    async def _persist_experience_record(
        self,
        *,
        organization_id: str,
        member_id: str,
        task_id: str | None,
        conversation_id: str | None,
        memory: MemoryItem | None,
        conflict_group_id: str,
        layer: str,
        kind: str,
        outcome: str,
        summary_text: str,
        source: dict[str, Any],
        evidence: dict[str, Any],
        score: dict[str, Any],
        confidence_score: float,
        reuse_score: float,
        decision: str,
        trace_id: str | None,
        now: str,
    ) -> MemoryExperienceRecord:
        data = {
            "experience_id": new_id("memexp"),
            "organization_id": organization_id,
            "member_id": member_id,
            "task_id": task_id,
            "conversation_id": conversation_id,
            "memory_id": memory.memory_id if memory else None,
            "conflict_group_id": memory.conflict_group_id if memory else conflict_group_id,
            "layer": layer,
            "kind": kind,
            "outcome": outcome,
            "summary_text": str(redact(summary_text)),
            "source": redact(source),
            "evidence": _redact_memory_evidence(evidence),
            "score": redact(score),
            "confidence_score": confidence_score,
            "reuse_score": reuse_score,
            "decision": decision,
            "status": "recorded",
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_experience_record(data)
        return _experience_record(data)

    async def _persist_conflict_record(
        self,
        *,
        organization_id: str,
        member_id: str | None,
        memory_id: str | None,
        related_memory_id: str | None,
        candidate_id: str | None,
        conflict_group_id: str,
        conflict_type: str,
        status: str,
        resolution: str | None,
        summary_text: str,
        source: dict[str, Any],
        evidence: dict[str, Any],
        trace_id: str | None,
        now: str,
    ) -> MemoryConflictRecord:
        data = {
            "conflict_id": new_id("memconf"),
            "organization_id": organization_id,
            "member_id": member_id,
            "memory_id": memory_id,
            "related_memory_id": related_memory_id,
            "candidate_id": candidate_id,
            "conflict_group_id": conflict_group_id,
            "conflict_type": conflict_type,
            "status": status,
            "resolution": resolution,
            "summary_text": str(redact(summary_text)),
            "source": redact(source),
            "evidence": redact(evidence),
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_conflict_record(data)
        return _conflict_record(data)

    async def _persist_reuse_feedback(
        self,
        *,
        organization_id: str,
        member_id: str,
        retrieval_id: str,
        memory_id: str,
        task_id: str | None,
        feedback_type: str,
        rating: float,
        source: dict[str, Any],
        evidence: dict[str, Any],
        trace_id: str | None,
        now: str,
    ) -> MemoryReuseFeedback:
        data = {
            "feedback_id": new_id("memfb"),
            "organization_id": organization_id,
            "member_id": member_id,
            "retrieval_id": retrieval_id,
            "memory_id": memory_id,
            "task_id": task_id,
            "feedback_type": feedback_type,
            "rating": rating,
            "source": redact(source),
            "evidence": _redact_memory_evidence(evidence),
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_reuse_feedback(data)
        return _reuse_feedback(data)

    async def _insert_candidate(
        self,
        *,
        organization_id: str,
        member_id: str,
        source: dict[str, Any],
        proposed_layer: str,
        proposed_kind: str,
        proposed_scope_type: str,
        proposed_scope_id: str | None,
        summary_text: str,
        payload: dict[str, Any],
        score: dict[str, Any],
        final_score: float,
        sensitivity: str,
        decision: str,
        decision_reason: str | None,
        now: str,
    ) -> MemoryCandidate:
        data = {
            "candidate_id": new_id("memcand"),
            "organization_id": organization_id,
            "member_id": member_id,
            "user_id": DEFAULT_USER_ID,
            "source": source,
            "proposed_layer": proposed_layer,
            "proposed_kind": proposed_kind,
            "proposed_scope_type": proposed_scope_type,
            "proposed_scope_id": proposed_scope_id,
            "summary_text": summary_text,
            "payload": redact(payload),
            "score": redact(score),
            "final_score": final_score,
            "sensitivity": sensitivity,
            "decision": decision,
            "decision_reason": decision_reason,
            "decided_at": now if decision != "pending" else None,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_candidate(data)
        return _memory_candidate(data)

    async def _insert_memory_from_candidate(
        self,
        candidate: dict[str, Any],
        *,
        decision: str,
        trace_id: str | None,
        now: str,
        supersede_query: str | None = None,
        quality_score: float | None = None,
        quality_breakdown: dict[str, Any] | None = None,
        conflict_group_id: str | None = None,
        retention_policy: str | None = None,
    ) -> MemoryItem:
        write_span = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_WRITE,
            "write memory item",
            metadata={"candidate_id": candidate["candidate_id"]},
        )
        old_memory: dict[str, Any] | None = None
        if supersede_query:
            conflict_span = await self._start_span(
                trace_id,
                TraceSpanType.MEMORY_CONFLICT,
                "check memory conflict",
                parent_span_id=write_span,
            )
            matches = await self._repo.search_memory_items(
                organization_id=candidate["organization_id"],
                member_id=str(candidate["member_id"]),
                query=supersede_query,
                limit=1,
                include_archived=False,
                include_sensitive=False,
            )
            old_memory = matches[0] if matches else None
            if old_memory is None:
                fallback_rows = await self._repo.list_memory_items(
                    member_id=str(candidate["member_id"]),
                    status="active",
                    limit=50,
                )
                normalized_query = _normalize(supersede_query)
                for row in fallback_rows:
                    normalized_summary = str(
                        row.get("normalized_summary")
                        or _normalize(str(row.get("summary_text") or ""))
                    )
                    if normalized_query and (
                        normalized_query in normalized_summary
                        or normalized_summary in normalized_query
                    ):
                        old_memory = row
                        break
            await self._end_span(
                conflict_span,
                output_data={
                    "superseded_memory_id": old_memory["memory_id"] if old_memory else None
                },
            )
        memory_id = new_id("mem")
        candidate_score = candidate.get("score") if isinstance(candidate.get("score"), dict) else {}
        breakdown = quality_breakdown or candidate_score.get("quality_breakdown") or {}
        quality_value = float(
            quality_score
            if quality_score is not None
            else candidate_score.get("quality_score", candidate["final_score"])
        )
        version_index = 1
        memory_conflict_group_id = conflict_group_id
        if old_memory is not None:
            version_index = int(old_memory.get("version_index") or 1) + 1
            memory_conflict_group_id = (
                str(old_memory.get("conflict_group_id") or f"grp_{old_memory['memory_id']}")
            )
        elif memory_conflict_group_id is None:
            memory_conflict_group_id = f"grp_{memory_id}"
        data = {
            "memory_id": memory_id,
            "organization_id": candidate["organization_id"],
            "member_id": candidate["member_id"],
            "user_id": candidate["user_id"],
            "layer": candidate["proposed_layer"],
            "kind": candidate["proposed_kind"],
            "scope_type": candidate["proposed_scope_type"],
            "scope_id": candidate["proposed_scope_id"],
            "memory_class": _memory_class_for_kind(
                candidate["proposed_kind"],
                layer=candidate["proposed_layer"],
            ),
            "scope_policy": _scope_policy_for_memory(
                scope_type=candidate["proposed_scope_type"],
            ),
            "summary_text": candidate["summary_text"],
            "payload": candidate["payload"],
            "source": candidate["source"],
            "confidence": quality_value,
            "importance": _importance_for_kind(candidate["proposed_kind"]),
            "sensitivity": candidate["sensitivity"],
            "durability": _durability_for_kind(
                candidate["proposed_kind"],
                layer=candidate["proposed_layer"],
                retention_policy=retention_policy
                or _retention_policy_for_kind(candidate["proposed_kind"]),
            ),
            "freshness_state": "fresh",
            "valid_from": now,
            "valid_to": None,
            "supersedes": old_memory["memory_id"] if old_memory else None,
            "superseded_by": None,
            "status": "active",
            "quality_score": quality_value,
            "quality_breakdown": breakdown,
            "version_index": version_index,
            "conflict_group_id": memory_conflict_group_id,
            "conflict_status": "resolved" if old_memory else "clear",
            "reuse_score": float(breakdown.get("reuse", 0.0) or 0.0),
            "reuse_count": 0,
            "last_reused_at": None,
            "retention_policy": retention_policy or _retention_policy_for_kind(candidate["proposed_kind"]),
            "retention_reason": _retention_reason_for_kind(candidate["proposed_kind"]),
            "expires_reason": None,
            "expires_at": None,
            "stale_after": _stale_after_for_kind(
                candidate["proposed_kind"],
                layer=candidate["proposed_layer"],
                now=now,
            ),
            "evidence_strength": _evidence_strength_value(
                quality_score=quality_value,
                confidence=quality_value,
            ),
            "review_required": False,
            "embedding_status": "pending",
            "metadata": {
                "candidate_id": candidate["candidate_id"],
                "vector": "pending",
                "quality_breakdown": breakdown,
                "quality_score": quality_value,
                "version_index": version_index,
                "conflict_group_id": memory_conflict_group_id,
            },
            "created_at": now,
            "updated_at": now,
            "normalized_summary": _normalize(candidate["summary_text"]),
            "content_hash": _hash_text(_normalize(candidate["summary_text"])),
        }
        async with self._db.transaction():
            await self._repo.insert_memory_item(data)
            await self._repo.update_candidate_decision(
                candidate["candidate_id"],
                decision=decision,
                decision_reason=None,
                decided_at=now,
                updated_at=now,
            )
            vector_span = await self._start_span(
                trace_id,
                TraceSpanType.MEMORY_VECTOR_UPSERT,
                "write memory vector ref",
                parent_span_id=write_span,
                metadata={"memory_id": memory_id, "provider": "local"},
            )
            vector_ref_id = new_id("vec")
            vector_status = "skipped"
            vector_provider = "none"
            vector_model = "fts_fallback"
            vector_error: str | None = "MEMORY_VECTOR_UNAVAILABLE"
            vector_id = memory_id
            vector_collection = f"memory_{candidate['organization_id']}"
            if self._vector is not None:
                try:
                    vector_result = await self._vector.upsert_text(
                        collection_name=f"memory_{candidate['organization_id']}",
                        target_type="memory",
                        target_id=memory_id,
                        text=candidate["summary_text"],
                        organization_id=candidate["organization_id"],
                        metadata={
                            "memory_id": memory_id,
                            "member_id": candidate.get("member_id"),
                            "layer": candidate["proposed_layer"],
                            "kind": candidate["proposed_kind"],
                            "sensitivity": candidate["sensitivity"],
                        },
                        content_hash=data["content_hash"],
                        trace_id=trace_id,
                    )
                    vector_status = "active"
                    vector_provider = str(vector_result.metadata.get("provider") or "local")
                    vector_model = str(
                        vector_result.metadata.get("embedding_model") or "local_hash_v1"
                    )
                    vector_error = None
                    vector_id = (
                        vector_result.vector_ref_ids[0]
                        if vector_result.vector_ref_ids
                        else memory_id
                    )
                    vector_collection = str(
                        vector_result.metadata.get("provider_collection_name")
                        or f"memory_{candidate['organization_id']}"
                    )
                    await self._repo.update_memory_item(
                        memory_id,
                        {
                            "embedding_status": "indexed",
                            "metadata": {
                                **data["metadata"],
                                "vector": {
                                    "provider": vector_provider,
                                    "provider_id": vector_result.metadata.get("provider_id"),
                                    "model": vector_model,
                                    "collection_name": vector_collection,
                                    "status": vector_status,
                                    "fallback_chain": vector_result.metadata.get(
                                        "fallback_chain", []
                                    ),
                                    "degraded_reason": vector_result.metadata.get(
                                        "degraded_reason"
                                    ),
                                },
                            },
                            "quality_score": quality_value,
                            "quality_breakdown": breakdown,
                            "version_index": version_index,
                            "conflict_group_id": memory_conflict_group_id,
                            "conflict_status": "resolved" if old_memory else "clear",
                            "reuse_score": float(breakdown.get("reuse", 0.0) or 0.0),
                            "reuse_count": 0,
                            "last_reused_at": None,
                            "retention_policy": retention_policy
                            or _retention_policy_for_kind(candidate["proposed_kind"]),
                            "retention_reason": _retention_reason_for_kind(
                                candidate["proposed_kind"]
                            ),
                            "updated_at": now,
                        },
                    )
                except Exception:
                    vector_error = "MEMORY_VECTOR_UPSERT_FAILED"
                    await self._repo.update_memory_item(
                        memory_id,
                        {
                            "embedding_status": "degraded",
                            "metadata": {
                                **data["metadata"],
                                "vector": {
                                    "provider": vector_provider,
                                    "model": vector_model,
                                    "status": "degraded",
                                    "error_code": vector_error,
                                },
                            },
                            "quality_score": quality_value,
                            "quality_breakdown": breakdown,
                            "version_index": version_index,
                            "conflict_group_id": memory_conflict_group_id,
                            "conflict_status": "resolved" if old_memory else "clear",
                            "reuse_score": float(breakdown.get("reuse", 0.0) or 0.0),
                            "reuse_count": 0,
                            "last_reused_at": None,
                            "retention_policy": retention_policy
                            or _retention_policy_for_kind(candidate["proposed_kind"]),
                            "retention_reason": _retention_reason_for_kind(
                                candidate["proposed_kind"]
                            ),
                            "updated_at": now,
                        },
                    )
            await self._repo.insert_vector_ref(
                vector_ref_id=vector_ref_id,
                organization_id=candidate["organization_id"],
                memory_id=memory_id,
                collection_name=vector_collection,
                vector_id=vector_id,
                embedding_provider=vector_provider,
                embedding_model=vector_model,
                content_hash=data["content_hash"],
                status=vector_status,
                last_synced_at=now if vector_status == "active" else None,
                error_code=vector_error,
                created_at=now,
                updated_at=now,
            )
            await self._end_span(
                vector_span,
                output_data={
                    "memory_id": memory_id,
                    "status": vector_status,
                    "provider": vector_provider,
                    "error_code": vector_error,
                },
            )
            if old_memory:
                await self._repo.update_memory_item(
                    old_memory["memory_id"],
                    {
                        "status": "superseded",
                        "freshness_state": "superseded",
                        "valid_to": now,
                        "conflict_group_id": memory_conflict_group_id,
                        "conflict_status": "superseded",
                        "expires_reason": "superseded_by_newer_memory",
                        "expires_at": now,
                        "superseded_by": memory_id,
                        "updated_at": now,
                        "metadata": {
                            **old_memory.get("metadata", {}),
                            "superseded_by": memory_id,
                        },
                    },
                )
                await self._repo.insert_relation(
                    relation_id=new_id("memrel"),
                    organization_id=candidate["organization_id"],
                    source_memory_id=memory_id,
                    target_memory_id=old_memory["memory_id"],
                    relation_type="supersedes",
                    evidence={"candidate_id": candidate["candidate_id"]},
                    created_at=now,
                )
                await self._repo.insert_conflict_record(
                    {
                        "conflict_id": new_id("memconf"),
                        "organization_id": candidate["organization_id"],
                        "member_id": candidate.get("member_id"),
                        "memory_id": memory_id,
                        "related_memory_id": old_memory["memory_id"],
                        "candidate_id": candidate["candidate_id"],
                        "conflict_group_id": memory_conflict_group_id,
                        "conflict_type": "superseded_by_correction"
                        if candidate["proposed_kind"] == "correction"
                        else "version_supersede",
                        "status": "resolved",
                        "resolution": "newer_memory_supersedes_old",
                        "summary_text": str(redact(candidate["summary_text"])),
                        "source": redact(candidate.get("source", {})),
                        "evidence": {
                            "superseded_memory_id": old_memory["memory_id"],
                            "new_memory_id": memory_id,
                            "candidate_id": candidate["candidate_id"],
                        },
                        "trace_id": trace_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
        await self._audit.write_event(
            actor_type="system",
            action=(
                "memory.correction_applied"
                if candidate["proposed_kind"] == "correction"
                else "memory.created"
            ),
            object_type="memory",
            object_id=memory_id,
            summary="长期记忆已写入",
            risk_level=RiskLevel.R1,
            payload={
                "candidate_id": candidate["candidate_id"],
                "memory_id": memory_id,
                "summary": candidate["summary_text"],
                "supersedes": old_memory["memory_id"] if old_memory else None,
            },
            trace_id=trace_id,
        )
        await self._end_span(
            write_span,
            output_data={
                "candidate_id": candidate["candidate_id"],
                "memory_id": memory_id,
                "decision": decision,
                "embedding_status": vector_status,
            },
        )
        memory = await self._repo.get_memory_item(memory_id)
        if memory is None:
            raise AppError(ErrorCode.MEMORY_WRITE_FAILED, "记忆写入后无法读取", status_code=500)
        return _memory_item(memory)

    async def _before_memory_write_candidate(
        self,
        candidate: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        if self._chat_hook_runtime is None:
            return candidate
        source = _normalize_memory_source_dict(dict(candidate.get("source") or {}))
        hook_result = await self._chat_hook_runtime.run_before_memory_write(
            {
                "trace_id": trace_id or source.get("trace_id"),
                "conversation_id": source.get("conversation_id"),
                "turn_id": source.get("turn_id"),
                "member_id": candidate.get("member_id"),
                "session_id": None,
                "channel": source.get("channel"),
                "payload": {
                    "candidate_id": candidate.get("candidate_id"),
                    "summary_text": candidate.get("summary_text"),
                    "source": source,
                },
            }
        )
        if hook_result.get("blocked"):
            raise AppError(
                ErrorCode.MEMORY_POLICY_BLOCKED,
                "记忆写入被 hook 治理阻断",
                status_code=422,
                details={"reason_code": hook_result.get("reason_code")},
            )
        rewritten = dict(hook_result.get("rewritten_payload") or {})
        if "source" not in rewritten:
            return {**candidate, "source": source}
        return {**candidate, "source": _normalize_memory_source_dict(dict(rewritten["source"]))}

    async def _set_memory_status(
        self,
        memory_id: str,
        *,
        status: str,
        trace_type: TraceSpanType,
        audit_action: str,
        audit_summary: str,
        trace_id: str | None,
    ) -> MemoryItem:
        existing = await self._repo.get_memory_item(memory_id)
        if existing is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在", status_code=404)
        span_id = await self._start_span(
            trace_id,
            trace_type,
            audit_summary,
            metadata={"memory_id": memory_id, "status": status},
        )
        now = utc_now_iso()
        freshness_state = (
            "stale"
            if status in {"archived", "deleted"}
            else "fresh"
        )
        await self._repo.update_memory_item(
            memory_id,
            {
                "status": status,
                "freshness_state": freshness_state,
                "updated_at": now,
            },
        )
        await self._end_span(span_id, output_data={"memory_id": memory_id, "status": status})
        await self._audit.write_event(
            actor_type="system",
            action=audit_action,
            object_type="memory",
            object_id=memory_id,
            summary=audit_summary,
            risk_level=RiskLevel.R1,
            payload={"memory_id": memory_id, "summary": existing["summary_text"]},
            trace_id=trace_id,
        )
        return await self.get_memory(memory_id)

    def _classify_command(
        self,
        text: str,
        *,
        force: bool = False,
        allow_implicit: bool = False,
    ) -> MemoryCommand | None:
        stripped = text.strip()
        if not stripped:
            return None
        if any(marker in stripped for marker in BLOCK_MARKERS):
            return MemoryCommand(
                kind="block",
                memory_kind="blocked_preference",
                layer=MemoryLayer.SEMANTIC.value,
                summary=_clean_summary(stripped),
                score=0.0,
                explicit=True,
            )
        correction = _parse_correction(stripped)
        if correction is not None:
            return MemoryCommand(
                kind="correction",
                memory_kind="correction",
                layer=MemoryLayer.TEMPORAL.value,
                summary=correction["summary"],
                supersede_query=correction["old"],
                score=0.9,
                explicit=True,
            )
        explicit_remember = _is_explicit_remember_command(stripped)
        if force or explicit_remember:
            summary = _clean_summary(stripped)
            memory_kind = _kind_for_summary(summary)
            if force and not explicit_remember:
                score = 0.5
            elif any(
                stripped.startswith(marker)
                for marker in (
                    "\u8bb0\u4f4f",
                    "\u8bf7\u8bb0\u4f4f",
                    "\u518d\u8bb0\u4f4f",
                    "\u5e2e\u6211\u8bb0\u4f4f",
                    "???",
                    "????",
                )
            ):
                score = 0.85
            else:
                score = 0.75
            return MemoryCommand(
                kind="remember",
                memory_kind=memory_kind,
                layer=MemoryLayer.PROCEDURAL.value
                if memory_kind == "skill_candidate"
                else MemoryLayer.SEMANTIC.value,
                summary=summary,
                score=score,
                explicit=explicit_remember,
                review_required=memory_kind == "skill_candidate",
            )
        if allow_implicit:
            implicit = _implicit_memory_command(stripped)
            if implicit is not None:
                return implicit
        return None

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        parent_span_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            parent_span_id=parent_span_id,
            input_data=input_data,
            metadata=metadata,
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=output_data,
                error_code=error_code,
            )


def _memory_candidate(row: dict[str, Any]) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=row["candidate_id"],
        organization_id=row["organization_id"],
        member_id=row.get("member_id"),
        user_id=row["user_id"],
        source=row["source"],
        proposed_layer=MemoryLayer(row["proposed_layer"]),
        proposed_kind=row["proposed_kind"],
        proposed_scope_type=row["proposed_scope_type"],
        proposed_scope_id=row.get("proposed_scope_id"),
        summary_text=row["summary_text"],
        payload=row.get("payload", {}),
        score=row.get("score", {}),
        final_score=float(row["final_score"]),
        sensitivity=row["sensitivity"],
        decision=row["decision"],
        decision_reason=row.get("decision_reason"),
        decided_at=row.get("decided_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _memory_item(row: dict[str, Any]) -> MemoryItem:
    return MemoryItem(
        memory_id=row["memory_id"],
        organization_id=row["organization_id"],
        member_id=row.get("member_id"),
        user_id=row["user_id"],
        layer=MemoryLayer(row["layer"]),
        kind=row["kind"],
        memory_class=_memory_class_for_row(row),
        scope_type=row["scope_type"],
        scope_id=row.get("scope_id"),
        scope_policy=_scope_policy_for_row(row),
        summary_text=row["summary_text"],
        payload=row.get("payload", {}),
        source=row["source"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        sensitivity=row["sensitivity"],
        valid_from=row.get("valid_from"),
        valid_to=row.get("valid_to"),
        supersedes=row.get("supersedes"),
        correction_status=_correction_status(row),
        status=row["status"],
        last_accessed_at=row.get("last_accessed_at"),
        access_count=int(row.get("access_count") or 0),
        quality_score=float(row.get("quality_score", 0.5) or 0.5),
        quality_breakdown=row.get("quality_breakdown", {}),
        version_index=int(row.get("version_index") or 1),
        conflict_group_id=row.get("conflict_group_id"),
        conflict_status=row.get("conflict_status", "clear"),
        reuse_score=float(row.get("reuse_score", 0.0) or 0.0),
        reuse_count=int(row.get("reuse_count") or 0),
        last_reused_at=row.get("last_reused_at"),
        retention_policy=row.get("retention_policy", "standard"),
        durability=_durability_for_row(row),
        freshness_state=_freshness_state_for_row(row),
        retention_reason=row.get("retention_reason"),
        expires_reason=row.get("expires_reason"),
        superseded_by=_superseded_by(row),
        expires_at=row.get("expires_at") or row.get("valid_to"),
        stale_after=row.get("stale_after"),
        evidence_strength=_evidence_strength_for_row(row),
        review_required=bool(row.get("review_required", False)),
        embedding_status=row.get("embedding_status", "pending"),
        metadata=row.get("metadata", {}),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _memory_summary_for_reply(memories: list[MemoryItem]) -> str:
    summaries: list[str] = []
    for memory in memories[:2]:
        summary = str(redact(memory.summary_text)).strip()
        if summary:
            summaries.append(summary)
    if not summaries:
        return "这条新记忆"
    joined = "；".join(summaries)
    return joined[:220] + ("..." if len(joined) > 220 else "")


def _memory_row_allowed(
    row: dict[str, Any],
    *,
    organization_id: str,
    member_id: str,
    request: MemorySearchApiRequest,
) -> bool:
    if row.get("organization_id") != organization_id:
        return False
    status = row.get("status")
    if status == "active":
        pass
    elif not request.include_archived or status not in {"archived"}:
        return False
    if row.get("valid_to") and str(row["valid_to"]) <= utc_now_iso():
        if request.freshness_policy != "allow_expired":
            return False
    if request.layers and MemoryLayer(row["layer"]) not in request.layers:
        return False
    if request.memory_classes and _memory_class_for_row(row) not in request.memory_classes:
        return False
    if request.durability_filter and _durability_for_row(row) not in request.durability_filter:
        return False
    sensitivity = str(row.get("sensitivity") or "low")
    if not request.include_sensitive and sensitivity in {
        "high",
        "secret",
        "credential",
        "wallet",
    }:
        return False
    scope_type = row.get("scope_type")
    scope_id = row.get("scope_id")
    if scope_type in {"user", "organization"}:
        return request.include_cross_session or not _is_cross_session_memory(
            row,
            conversation_id=request.exclude_conversation_id or request.conversation_id,
        )
    if row.get("member_id") == member_id or scope_id == member_id:
        if not request.include_cross_session and _is_cross_session_memory(
            row,
            conversation_id=request.exclude_conversation_id or request.conversation_id,
        ):
            return False
        return True
    if scope_type == "asset":
        return request.include_asset_scoped and (
            "*" in request.asset_scope_ids or str(scope_id) in request.asset_scope_ids
        )
    return False


def _suppression_reason_for_memory(
    row: dict[str, Any],
    *,
    request: MemorySearchApiRequest,
) -> str | None:
    status = str(row.get("status") or "")
    if status in {"superseded", "deleted", "archived"} and not request.include_archived:
        if status == "superseded":
            return "status_superseded"
        return f"status_{status}"
    freshness_state = _freshness_state_for_row(row)
    if freshness_state == "expired" and request.freshness_policy != "allow_expired":
        return "expired"
    if freshness_state in {"stale", "aging"} and request.freshness_policy == "exclude_stale":
        return freshness_state
    if freshness_state == "superseded" and request.freshness_policy != "allow_superseded":
        return "status_superseded"
    if request.memory_classes and _memory_class_for_row(row) not in request.memory_classes:
        return "memory_class_filtered"
    if request.durability_filter and _durability_for_row(row) not in request.durability_filter:
        return "durability_filtered"
    sensitivity = str(row.get("sensitivity") or "low")
    if not request.include_sensitive and sensitivity in {
        "high",
        "secret",
        "credential",
        "wallet",
    }:
        return f"sensitivity_{sensitivity}"
    return None


def _semantic_score(row: dict[str, Any]) -> float:
    source = str(row.get("retrieval_source") or "")
    raw = float(row.get("rank_score", row.get("importance", 0.0)) or 0.0)
    if source == "semantic_vector":
        return max(0.0, min(1.0, raw))
    if source == "fts_supplement":
        return max(0.15, min(0.75, raw if raw <= 1 else 0.65))
    if source == "fts_fallback":
        return max(0.1, min(0.65, raw if raw <= 1 else 0.55))
    return max(0.05, min(0.5, raw))


def _recency_score(row: dict[str, Any]) -> float:
    freshness_state = _freshness_state_for_row(row)
    if freshness_state == "fresh":
        return 0.88
    if freshness_state == "aging":
        return 0.68
    if freshness_state == "stale":
        return 0.28
    if freshness_state in {"superseded", "expired"}:
        return 0.08
    if row.get("updated_at"):
        return 0.7
    if row.get("created_at"):
        return 0.55
    return 0.4


def _canonical_memory_source_type(
    source_type: str,
    *,
    conversation_id: str | None = None,
) -> str:
    value = str(source_type or "").strip().lower()
    mapping = {
        "manual": "external_ingest",
        "conversation": "conversation_turn",
        "chat": "conversation_turn",
        "message": "conversation_turn",
        "turn": "conversation_turn",
        "tool": "tool_result",
        "tool_result": "tool_result",
        "task": "task_result",
        "task_experience": "task_result",
        "approval": "approval_resolution",
        "approval_resolution": "approval_resolution",
        "external": "external_ingest",
        "external_ingest": "external_ingest",
        "retrieval_feedback": "external_ingest",
        "agent_workbench_reflection": "conversation_turn",
        "turn_recovery": "task_result",
    }
    if value in mapping:
        return mapping[value]
    if conversation_id and value in {"", "unknown"}:
        return "conversation_turn"
    return value or "external_ingest"


def _normalize_memory_source_dict(source: dict[str, Any]) -> dict[str, Any]:
    source = dict(source or {})
    source["type"] = _canonical_memory_source_type(
        str(source.get("type") or "unknown"),
        conversation_id=source.get("conversation_id"),
    )
    source.setdefault("captured_at", utc_now_iso())
    source.setdefault("channel", "local")
    source.setdefault("tool_call_id", None)
    source.setdefault("approval_id", None)
    return source


def _source_reliability(row: dict[str, Any]) -> float:
    source_value = row.get("source")
    source = source_value if isinstance(source_value, dict) else {}
    source_type = str(source.get("type") or "")
    if source_type in {
        "conversation_turn",
        "tool_result",
        "task_result",
        "approval_resolution",
        "external_ingest",
    }:
        return 0.95
    if source_type in {"chat", "message", "turn", "retrieval_feedback"}:
        return 0.75
    return 0.65


def _public_memory_source(source_value: Any) -> dict[str, Any]:
    source = source_value if isinstance(source_value, dict) else {}
    return {
        "type": _canonical_memory_source_type(str(source.get("type") or "unknown")),
        "conversation_id": source.get("conversation_id"),
        "task_id": source.get("task_id"),
        "step_id": source.get("step_id"),
        "turn_id": None,
        "message_id": None,
        "tool_call_id": None,
        "approval_id": None,
        "channel": source.get("channel"),
        "captured_at": source.get("captured_at"),
        "trace_id": None,
    }


_MEMORY_EVIDENCE_SECRET_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "local_path",
    "localstorage",
    "password",
    "profile_dir",
    "profile_path",
    "secret",
    "set-cookie",
    "storage_state",
    "token",
}


def _redact_memory_evidence(value: Any) -> Any:
    redacted = redact(value)
    return _redact_memory_paths(redacted)


def _redact_memory_paths(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("_", "").replace("-", "")
            if normalized_key in {
                item_key.replace("_", "").replace("-", "")
                for item_key in _MEMORY_EVIDENCE_SECRET_KEYS
            }:
                output[key] = "[REDACTED]"
            else:
                output[key] = _redact_memory_paths(item)
        return output
    if isinstance(value, list):
        return [_redact_memory_paths(item) for item in value]
    if isinstance(value, str) and _looks_like_local_path(value):
        return "[REDACTED_LOCAL_PATH]"
    return value


def _looks_like_local_path(value: str) -> bool:
    return bool(
        re.search(r"\b[A-Za-z]:[\\/]", value)
        or re.search(r"(^|[\\/])(Users|home)[\\/][^\\/]+", value)
    )


def _explicitness_score(row: dict[str, Any]) -> float:
    metadata_value = row.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    source_value = row.get("source")
    source = source_value if isinstance(source_value, dict) else {}
    if metadata.get("explicit") is True or source.get("type") in {
        "conversation_turn",
        "external_ingest",
    }:
        return 0.95
    if row.get("kind") in {
        "preference",
        "project_fact",
        "correction",
        "task_experience",
        "procedural_experience",
    }:
        return 0.78
    return 0.6


def _supersede_score(row: dict[str, Any]) -> float:
    if row.get("status") == "superseded":
        return 0.0
    if row.get("supersedes"):
        return 0.9
    return 0.75


def _sensitivity_score(row: dict[str, Any]) -> float:
    sensitivity = str(row.get("sensitivity") or "low")
    if sensitivity in {"secret", "credential", "wallet"}:
        return 0.0
    if sensitivity == "high":
        return 0.2
    if sensitivity == "medium":
        return 0.65
    return 0.9


def _conversation_score(row: dict[str, Any], conversation_id: str | None) -> float:
    if not conversation_id:
        return 0.5
    source_value = row.get("source")
    source = source_value if isinstance(source_value, dict) else {}
    return 0.9 if source.get("conversation_id") == conversation_id else 0.45


def _member_scope_score(row: dict[str, Any], member_id: str) -> float:
    if row.get("member_id") == member_id or row.get("scope_id") == member_id:
        return 0.9
    if row.get("scope_type") in {"user", "organization"}:
        return 0.65
    return 0.45


def _provider_quality_score(row: dict[str, Any]) -> float:
    provider = str(row.get("provider") or "")
    model = str(row.get("embedding_model") or "")
    if "local_hash" in model or provider == "local":
        return 0.55
    if provider in {"local_model", "chroma", "external_compatible"}:
        return 0.85
    return 0.6


def _memory_conflict_notes(row: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    freshness_state = _freshness_state_for_row(row)
    if freshness_state == "superseded":
        notes.append("superseded_by_newer_memory")
    if freshness_state == "expired":
        notes.append("expired_memory")
    if freshness_state == "stale":
        notes.append("stale_memory")
    if str(row.get("conflict_status") or "") in {"needs_review", "conflicted"}:
        notes.append(f"conflict_{row.get('conflict_status')}")
    return notes


def _suppressed_item(
    *,
    target_id: str,
    reason: str,
    sensitivity: str | None,
    score: float,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "reason": reason,
        "sensitivity": sensitivity,
        "selection_score": round(score, 4),
        "metadata": metadata,
    }


def _memory_validity(row: dict[str, Any]) -> str:
    freshness_state = _freshness_state_for_row(row)
    if freshness_state == "superseded":
        return "superseded"
    if freshness_state == "expired":
        return "expired"
    if str(row.get("conflict_status") or "") in {"conflicted", "needs_review"}:
        return "conflicted"
    return "current"


def _candidate_row(candidate: MemoryCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "organization_id": candidate.organization_id,
        "member_id": candidate.member_id,
        "user_id": candidate.user_id,
        "source": candidate.source.model_dump(mode="json"),
        "proposed_layer": candidate.proposed_layer.value,
        "proposed_kind": candidate.proposed_kind,
        "proposed_scope_type": candidate.proposed_scope_type,
        "proposed_scope_id": candidate.proposed_scope_id,
        "summary_text": candidate.summary_text,
        "payload": candidate.payload,
        "score": candidate.score,
        "final_score": candidate.final_score,
        "sensitivity": candidate.sensitivity,
        "decision": candidate.decision,
    }


def _experience_record(row: dict[str, Any]) -> MemoryExperienceRecord:
    return MemoryExperienceRecord(
        experience_id=row["experience_id"],
        organization_id=row["organization_id"],
        member_id=row.get("member_id"),
        task_id=row.get("task_id"),
        conversation_id=row.get("conversation_id"),
        memory_id=row.get("memory_id"),
        conflict_group_id=row.get("conflict_group_id"),
        layer=MemoryLayer(row["layer"]),
        kind=row["kind"],
        outcome=row["outcome"],
        summary_text=row["summary_text"],
        source=row["source"],
        evidence=row.get("evidence", {}),
        score=row.get("score", {}),
        confidence_score=float(row.get("confidence_score") or 0.0),
        reuse_score=float(row.get("reuse_score") or 0.0),
        decision=row["decision"],
        status=row.get("status", "recorded"),
        trace_id=row.get("trace_id"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _conflict_record(row: dict[str, Any]) -> MemoryConflictRecord:
    return MemoryConflictRecord(
        conflict_id=row["conflict_id"],
        organization_id=row["organization_id"],
        member_id=row.get("member_id"),
        memory_id=row.get("memory_id"),
        related_memory_id=row.get("related_memory_id"),
        candidate_id=row.get("candidate_id"),
        conflict_group_id=row["conflict_group_id"],
        conflict_type=row["conflict_type"],
        status=row["status"],
        resolution=row.get("resolution"),
        summary_text=row["summary_text"],
        source=row.get("source", {"type": "unknown"}),
        evidence=row.get("evidence", {}),
        trace_id=row.get("trace_id"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _reuse_feedback(row: dict[str, Any]) -> MemoryReuseFeedback:
    return MemoryReuseFeedback(
        feedback_id=row["feedback_id"],
        organization_id=row["organization_id"],
        member_id=row.get("member_id"),
        retrieval_id=row["retrieval_id"],
        memory_id=row["memory_id"],
        task_id=row.get("task_id"),
        feedback_type=row["feedback_type"],
        rating=float(row.get("rating") or 0.0),
        source=row.get("source", {"type": "unknown"}),
        evidence=row.get("evidence", {}),
        trace_id=row.get("trace_id"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _clean_summary(text: str) -> str:
    summary = text.strip()
    for prefix in ("请记住", "记住", "记住：", "记住:", "我的偏好是", "这个项目规则是"):
        summary = summary.replace(prefix, "", 1).strip()
    summary = summary.lstrip("：:，,。 ")
    return str(redact(summary or text.strip()))

def _sensitive_secret_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in SECRET_TOKEN_PATTERNS:
        if pattern.search(text):
            hits.append("secret_token_pattern")
    return hits



def _parse_correction(text: str) -> dict[str, str] | None:
    stripped = text.strip()
    if not stripped:
        return None
    normalized = stripped
    for prefix in (*EXTRA_CORRECTION_PREFIXES, "memory correction:", "Memory correction:"):
        if normalized.startswith(prefix):
            remainder = normalized.removeprefix(prefix).lstrip(" :：，,")
            if not remainder:
                return None
            summary = _clean_summary(remainder)
            subject = remainder.split(":", 1)[0].split("：", 1)[0].strip()
            return {
                "old": subject or summary,
                "summary": str(redact(f"纠正为：{summary}")),
            }
    if normalized.startswith("不是"):
        remainder = normalized.removeprefix("不是").strip()
        for separator in ("，是", ",是", "而是"):
            if separator in remainder:
                old, new = remainder.split(separator, 1)
                old = old.strip(" ，,:：")
                new = new.strip(" ，,:：")
                if new:
                    return {
                        "old": old or _clean_summary(normalized),
                        "summary": str(redact(f"纠正为：{new}")),
                    }
    for marker in ("改成", "换成", "纠正", "修正", "更正"):
        if normalized.startswith(marker):
            after = normalized.removeprefix(marker).strip(" ，,:：")
            if after:
                return {
                    "old": _clean_summary(normalized),
                    "summary": str(redact(f"纠正为：{after}")),
                }
    return None


def _is_explicit_memory_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if _looks_like_memory_query(stripped):
        return False
    return (
        any(marker in stripped for marker in BLOCK_MARKERS)
        or _parse_correction(stripped) is not None
        or _is_explicit_remember_command(stripped)
    )


def _is_explicit_forget_command(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    return any(marker in stripped for marker in ("忘记", "请忘记", "别再记", "不要再记", "清除这条记忆"))


def _is_explicit_remember_command(text: str) -> bool:
    stripped = text.strip()
    return any(stripped.startswith(marker) for marker in EXPLICIT_REMEMBER_PREFIXES) or any(
        marker in stripped for marker in EXTRA_EXPLICIT_REMEMBER_PREFIXES
    )


def _looks_like_memory_query(text: str) -> bool:
    if structured_summary_chat_request(text) or preference_application_request(text):
        return False
    if explicit_preference_recall_query(text):
        return True
    query_markers = ("记得", "还记得", "之前", "说过", "回忆", "复述", "什么")
    query_refs = ("偏好", "长期记忆", "记住", "记住了什么", "项目", "风格")
    return any(marker in text for marker in query_markers) and any(
        marker in text for marker in query_refs
    )


def _implicit_memory_command(text: str) -> MemoryCommand | None:
    summary = _clean_summary(text)
    if any(marker in text for marker in ("这个项目", "项目规则", "核心规则", "必须", "禁止")):
        return MemoryCommand(
            kind="implicit_project_fact",
            memory_kind="project_fact",
            layer=MemoryLayer.SEMANTIC.value,
            summary=summary,
            score=0.75,
            explicit=False,
        )
    if any(marker in text for marker in ("我喜欢", "我希望", "我的偏好", "以后回复", "以后输出")):
        return MemoryCommand(
            kind="implicit_preference",
            memory_kind="preference",
            layer=MemoryLayer.SEMANTIC.value,
            summary=summary,
            score=0.75,
            explicit=False,
        )
    if any(marker in text for marker in ("以后都按这个流程", "固定流程", "这个模板")):
        return MemoryCommand(
            kind="implicit_skill_candidate",
            memory_kind="skill_candidate",
            layer=MemoryLayer.PROCEDURAL.value,
            summary=summary,
            score=0.65,
            explicit=False,
            review_required=True,
        )
    return None


def _score_decision(command: MemoryCommand, *, final_score: float | None = None) -> MemoryScore:
    score_value = float(command.score if final_score is None else final_score)
    if command.review_required:
        return MemoryScore(
            final_score=score_value,
            decision="needs_review",
            reason="review_required",
            review_required=True,
        )
    if score_value < MIN_WRITE_SCORE:
        return MemoryScore(
            final_score=score_value,
            decision="discarded_low_value",
            reason="score_below_threshold",
        )
    return MemoryScore(final_score=score_value, decision="auto_written", reason=None)


def _kind_for_summary(summary: str) -> str:
    if "项目" in summary or "规则" in summary or "核心层" in summary:
        return "project_fact"
    if "流程" in summary or "模板" in summary:
        return "skill_candidate"
    if "偏好" in summary or "以后" in summary or "喜欢" in summary or "文档" in summary:
        return "preference"
    return "semantic_note"


def _memory_class_for_kind(kind: str, *, layer: str) -> str:
    if kind in {"preference", "correction"}:
        return "preference"
    if kind in {
        "project_fact",
        "knowledge_fact",
        "semantic_note",
    }:
        return "fact"
    if kind in {
        "task_experience",
        "episodic_experience",
        "task_failure_experience",
        "procedural_experience",
        "skill_candidate",
    }:
        return "experience"
    if layer in {
        MemoryLayer.WORKING.value,
        MemoryLayer.SESSION.value,
        MemoryLayer.TEMPORAL.value,
    }:
        return "transient_working_state"
    return "fact"


def _scope_policy_for_memory(*, scope_type: str) -> str:
    if scope_type == "asset":
        return "asset_scoped"
    if scope_type == "organization":
        return "organization_shared"
    if scope_type == "conversation":
        return "current_conversation"
    return "member_cross_session"


def _durability_for_kind(kind: str, *, layer: str, retention_policy: str) -> str:
    if kind == "correction":
        return "durable"
    if layer in {
        MemoryLayer.WORKING.value,
        MemoryLayer.SESSION.value,
        MemoryLayer.TEMPORAL.value,
    }:
        return "transient"
    if retention_policy in {"persistent", "review_required"}:
        return "durable"
    if retention_policy == "standard":
        return "session"
    return "durable"


def _stale_after_for_kind(kind: str, *, layer: str, now: str) -> str | None:
    if kind == "correction":
        return None
    if layer in {
        MemoryLayer.WORKING.value,
        MemoryLayer.SESSION.value,
        MemoryLayer.TEMPORAL.value,
    }:
        return now
    if kind == "skill_candidate":
        return now
    return None


def _memory_class_for_row(row: dict[str, Any]) -> str:
    value = str(row.get("memory_class") or "").strip()
    if value:
        return value
    return _memory_class_for_kind(str(row.get("kind") or ""), layer=str(row.get("layer") or "semantic"))


def _scope_policy_for_row(row: dict[str, Any]) -> str:
    value = str(row.get("scope_policy") or "").strip()
    if value:
        return value
    return _scope_policy_for_memory(scope_type=str(row.get("scope_type") or "member"))


def _durability_for_row(row: dict[str, Any]) -> str:
    value = str(row.get("durability") or "").strip()
    if value:
        return value
    return _durability_for_kind(
        str(row.get("kind") or ""),
        layer=str(row.get("layer") or "semantic"),
        retention_policy=str(row.get("retention_policy") or "standard"),
    )


def _freshness_state_for_row(row: dict[str, Any]) -> str:
    value = str(row.get("freshness_state") or "").strip()
    if value:
        return value
    status = str(row.get("status") or "")
    if status == "superseded":
        return "superseded"
    if row.get("valid_to") and str(row["valid_to"]) <= utc_now_iso():
        return "expired"
    if status in {"archived", "deleted"}:
        return "stale"
    if row.get("stale_after") and str(row.get("stale_after")) <= utc_now_iso():
        return "stale"
    if _durability_for_row(row) == "transient":
        return "aging"
    return "fresh"


def _superseded_by(row: dict[str, Any]) -> str | None:
    return str(
        row.get("superseded_by")
        or dict(row.get("metadata") or {}).get("superseded_by")
        or ""
    ).strip() or None


def _correction_status(row: dict[str, Any]) -> str | None:
    if str(row.get("kind") or "") != "correction":
        return None
    return "applied" if row.get("supersedes") else "not_found"


def _evidence_strength_value(*, quality_score: float, confidence: float) -> float:
    return round(max(0.05, min(1.0, quality_score * 0.65 + confidence * 0.35)), 4)


def _evidence_strength_for_row(row: dict[str, Any]) -> float:
    value = row.get("evidence_strength")
    if value is not None:
        return float(value)
    return _evidence_strength_value(
        quality_score=float(row.get("quality_score", row.get("importance", 0.5)) or 0.5),
        confidence=float(row.get("confidence", 0.5) or 0.5),
    )


def _is_cross_session_memory(row: dict[str, Any], *, conversation_id: str | None) -> bool:
    source = dict(row.get("source") or {})
    source_conversation_id = str(source.get("conversation_id") or "")
    if not source_conversation_id or not conversation_id:
        return _scope_policy_for_row(row) != "current_conversation"
    return source_conversation_id != conversation_id


def _recall_scope_applied(request: MemorySearchApiRequest) -> str:
    if request.include_cross_session:
        return str(request.recall_scope or "member_cross_session")
    return "current_conversation"


def _suppressed_reason_codes(row: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    suppressed_reason = row.get("suppressed_reason")
    if suppressed_reason:
        codes.append(str(suppressed_reason))
    codes.extend(str(item) for item in row.get("conflict_notes", []) if item)
    return list(dict.fromkeys(codes))


def _importance_for_kind(kind: str) -> float:
    if kind in {"preference", "project_fact", "correction"}:
        return 0.8
    if kind == "skill_candidate":
        return 0.65
    return 0.5


def _memory_block_title(kind: str, *, freshness_state: str = "fresh") -> str:
    titles = {
        "preference": "用户偏好",
        "project_fact": "项目事实",
        "correction": "用户纠错",
        "skill_candidate": "流程候选",
        "fact": "长期事实",
        "experience": "经验沉淀",
        "transient_working_state": "临时上下文",
    }
    title = titles.get(kind, "相关记忆")
    if freshness_state == "aging":
        return f"{title}（较旧）"
    return title


def _block_type_for_kind(kind: str) -> str:
    if kind == "transient_working_state":
        return "temporal"
    if kind in {"skill_candidate", "experience"}:
        return "procedural"
    return "semantic"


def _memory_quality_breakdown(
    *,
    summary_text: str,
    kind: str,
    source: dict[str, Any],
    text: str,
    command_kind: str,
) -> dict[str, float]:
    clean_summary = str(summary_text).strip()
    source_type = str((source or {}).get("type") or "")
    explicit_source = source_type in {
        "conversation_turn",
        "tool_result",
        "task_result",
        "approval_resolution",
        "external_ingest",
    }
    value = 0.72 if kind in {"preference", "project_fact", "correction"} else 0.58
    if command_kind == "block":
        value = 0.12
    if command_kind == "correction":
        value = max(value, 0.9)
    clarity = 0.92 if len(clean_summary) < 120 else 0.78 if len(clean_summary) < 220 else 0.64
    if len(clean_summary.split()) <= 3:
        clarity = min(clarity, 0.7)
    stability = 0.85 if kind in {"preference", "project_fact"} else 0.66
    if kind == "skill_candidate":
        stability = 0.58
    if command_kind == "correction":
        stability = max(stability, 0.82)
    sensitivity = 0.95
    if any(marker in text for marker in ("密码", "token", "cookie", "secret")):
        sensitivity = 0.12
    reuse = 0.82 if kind in {"preference", "project_fact", "correction"} else 0.62
    if explicit_source:
        value = min(0.98, value + 0.06)
        reuse = min(0.95, reuse + 0.04)
    conflict_risk = 0.05 if kind in {"preference", "project_fact"} else 0.14
    if command_kind == "block":
        conflict_risk = 0.02
    if command_kind == "correction":
        conflict_risk = 0.08
    return {
        "value": round(max(0.0, min(1.0, value)), 4),
        "clarity": round(max(0.0, min(1.0, clarity)), 4),
        "stability": round(max(0.0, min(1.0, stability)), 4),
        "sensitivity": round(max(0.0, min(1.0, sensitivity)), 4),
        "reuse": round(max(0.0, min(1.0, reuse)), 4),
        "conflict_risk": round(max(0.0, min(1.0, conflict_risk)), 4),
    }


def _memory_quality_score(breakdown: dict[str, float]) -> float:
    score = (
        breakdown.get("value", 0.0) * 0.32
        + breakdown.get("clarity", 0.0) * 0.16
        + breakdown.get("stability", 0.0) * 0.18
        + breakdown.get("sensitivity", 0.0) * 0.12
        + breakdown.get("reuse", 0.0) * 0.12
        + (1.0 - breakdown.get("conflict_risk", 0.0)) * 0.1
    )
    return max(0.0, min(1.0, score))


def _quality_score(row: dict[str, Any]) -> float:
    value = float(row.get("quality_score", row.get("importance", 0.0)) or 0.0)
    if value > 1.0:
        value = 1.0
    return max(0.05, value)


def _reuse_score(row: dict[str, Any]) -> float:
    reuse_score = float(row.get("reuse_score", 0.0) or 0.0)
    reuse_count = int(row.get("reuse_count") or 0)
    if reuse_count:
        reuse_score = max(reuse_score, min(1.0, reuse_count / 10))
    return max(0.0, min(1.0, reuse_score))


def _version_stability_score(row: dict[str, Any]) -> float:
    version = int(row.get("version_index") or 1)
    if row.get("status") == "superseded":
        return 0.05
    if str(row.get("conflict_status") or "") == "superseded":
        return 0.12
    if version <= 1:
        return 0.78
    if version <= 3:
        return 0.9
    return 0.96


def _conflict_safety_score(row: dict[str, Any]) -> float:
    conflict_status = str(row.get("conflict_status") or "clear")
    if conflict_status == "clear":
        return 0.95
    if conflict_status in {"resolved", "observed"}:
        return 0.72
    if conflict_status in {"needs_review", "conflicted"}:
        return 0.38
    if conflict_status == "superseded":
        return 0.08
    return 0.52


def _experience_kind_for_outcome(outcome: str, steps: list[dict[str, Any]]) -> str:
    if outcome == "failed":
        return "task_failure_experience"
    if any("skill" in str(step.get("step_type") or "") for step in steps):
        return "procedural_experience"
    if any("browser" in str(step.get("step_type") or "") for step in steps):
        return "episodic_experience"
    return "task_experience"


def _experience_layer_for_outcome(outcome: str, steps: list[dict[str, Any]]) -> str:
    if outcome == "failed":
        return MemoryLayer.EPISODIC.value
    if any("skill" in str(step.get("step_type") or "") for step in steps):
        return MemoryLayer.PROCEDURAL.value
    return MemoryLayer.EPISODIC.value


def _experience_quality_breakdown(
    *,
    summary_text: str,
    outcome: str,
    evidence: dict[str, Any],
    steps: list[dict[str, Any]],
    source: dict[str, Any],
) -> dict[str, float]:
    step_count = len(steps)
    has_result = bool(evidence.get("result") or evidence.get("artifact_refs"))
    value = 0.82 if outcome == "completed" else 0.58
    if has_result:
        value = min(0.95, value + 0.06)
    clarity = 0.9 if 18 <= len(summary_text) <= 240 else 0.68
    stability = 0.82 if outcome == "completed" else 0.48
    sensitivity = 0.92
    serialized = str(evidence)
    if any(marker in serialized.lower() for marker in ("token", "cookie", "password", "secret")):
        sensitivity = 0.42
    reuse = 0.74 if outcome == "completed" else 0.36
    if step_count >= 2:
        reuse = min(0.96, reuse + 0.12)
    if source.get("task_id"):
        clarity = min(0.96, clarity + 0.04)
    conflict_risk = 0.08 if outcome == "completed" else 0.22
    return {
        "value": round(value, 4),
        "clarity": round(clarity, 4),
        "stability": round(stability, 4),
        "sensitivity": round(sensitivity, 4),
        "reuse": round(reuse, 4),
        "conflict_risk": round(conflict_risk, 4),
        "step_count": float(step_count),
    }


def _experience_quality_score(breakdown: dict[str, float]) -> float:
    return _memory_quality_score(
        {
            "value": breakdown.get("value", 0.0),
            "clarity": breakdown.get("clarity", 0.0),
            "stability": breakdown.get("stability", 0.0),
            "sensitivity": breakdown.get("sensitivity", 0.0),
            "reuse": breakdown.get("reuse", 0.0),
            "conflict_risk": breakdown.get("conflict_risk", 0.0),
        }
    )


def _experience_sensitivity(outcome: str, evidence: dict[str, Any]) -> str:
    if outcome == "failed":
        return "low"
    serialized = str(evidence)
    if any(marker in serialized for marker in ("secret", "token", "cookie", "password")):
        return "medium"
    return "low"


def _experience_reuse_score(outcome: str, breakdown: dict[str, float]) -> float:
    base = breakdown.get("reuse", 0.0)
    if outcome == "failed":
        return max(0.08, base * 0.45)
    return max(0.15, min(1.0, base + 0.1))


def _retention_policy_for_kind(kind: str) -> str:
    if kind in {"preference", "project_fact", "correction"}:
        return "persistent"
    if kind == "skill_candidate":
        return "review_required"
    return "standard"


def _retention_reason_for_kind(kind: str) -> str | None:
    if kind == "skill_candidate":
        return "procedural_candidate_requires_review"
    if kind in {"preference", "project_fact"}:
        return "user_facing_long_term_context"
    if kind == "correction":
        return "corrected_truth_should_replace_previous_memory"
    return None


def _retention_policy_for_experience(outcome: str, score: float) -> str:
    if outcome == "failed":
        return "review_required"
    if score >= 0.8:
        return "persistent"
    return "standard"


def _reuse_feedback_delta(feedback_type: str, rating: float) -> float:
    normalized = max(-1.0, min(1.0, float(rating)))
    if feedback_type in {"helpful", "corrected"}:
        return max(0.02, 0.12 * max(0.0, normalized))
    if feedback_type in {"irrelevant", "stale"}:
        return -max(0.02, 0.08 * max(0.0, normalized))
    return normalized * 0.03


def _should_use_recent_fallback(query: str, intent: str | None) -> bool:
    if intent == "memory_query":
        return True
    return any(
        marker in query
        for marker in ("之前", "记得", "偏好", "说过", "项目规则", "喜欢", "风格")
    )


def _filter_reason(
    memory: dict[str, Any],
    *,
    request: MemorySearchApiRequest,
    include_sensitive: bool,
    include_asset_scoped: bool,
    asset_scope_ids: list[str],
) -> str | None:
    if memory["status"] != "active":
        if memory["status"] == "superseded":
            return "status_superseded"
        return f"status_{memory['status']}"
    freshness_state = _freshness_state_for_row(memory)
    if freshness_state == "expired" and request.freshness_policy != "allow_expired":
        return "expired"
    if freshness_state in {"stale", "aging"} and request.freshness_policy == "exclude_stale":
        return freshness_state
    if freshness_state == "superseded" and request.freshness_policy != "allow_superseded":
        return "status_superseded"
    if request.memory_classes and _memory_class_for_row(memory) not in request.memory_classes:
        return "memory_class_filtered"
    if request.durability_filter and _durability_for_row(memory) not in request.durability_filter:
        return "durability_filtered"
    if not include_sensitive and memory["sensitivity"] in {
        "high",
        "secret",
        "credential",
        "wallet",
    }:
        return f"sensitivity_{memory['sensitivity']}"
    if memory["scope_type"] == "asset" and (
        not include_asset_scoped or str(memory.get("scope_id")) not in asset_scope_ids
    ):
        return "asset_scope_requires_broker"
    return "not_relevant"


def _normalize(value: str) -> str:
    lowered = value.lower()
    return "".join(ch for ch in lowered if ch.isalnum())


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
