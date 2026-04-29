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
    MemoryBlock,
    MemoryBlockItem,
    MemoryCandidate,
    MemoryItem,
    MemoryLayer,
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
    MemoryExtractResponse,
    MemoryJobItem,
    MemorySearchApiRequest,
    MemorySearchApiResponse,
    MemoryUpdateRequest,
)
from app.services.audit import AuditEventService

DEFAULT_USER_ID = "user_local_owner"
MIN_WRITE_SCORE = 0.55

REMEMBER_MARKERS = ("记住", "请记住", "以后", "我的偏好", "这个项目规则")
EXPLICIT_REMEMBER_PREFIXES = ("记住", "请记住", "以后", "我的偏好", "这个项目规则")
BLOCK_MARKERS = ("不要记", "别记", "不要再记", "别再记")
CORRECTION_MARKERS = ("改成", "不是", "以后不")
WORKER_ID = "memory_worker_local"
JOB_STALE_AFTER_MINUTES = 10


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
        "semantic_score": 0.34,
        "recency_score": 0.12,
        "source_reliability": 0.12,
        "explicitness": 0.12,
        "supersede_status": 0.14,
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
                "supersede_status": _supersede_score(row),
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
    ) -> None:
        self._db = db
        self._repo = repo
        self._chat = chat_repo
        self._members = member_repo
        self._trace = trace_service
        self._audit = audit_service
        self._vector = vector_service
        self._retrieval_repo = retrieval_repo
        self._reranker = MemoryRerankService()
        self._safety = SafetyService()
        self._background_tasks: set[asyncio.Task[int]] = set()

    async def list_memories(
        self,
        *,
        member_id: str | None = None,
        status: str | None = None,
        layer: str | None = None,
        kind: str | None = None,
        sensitivity: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        rows = await self._repo.list_memory_items(
            member_id=member_id,
            status=status,
            layer=layer,
            kind=kind,
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
                degraded=degraded_reason is not None and "semantic_vector" not in retrieval_sources,
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
                        summary_text=row["summary_text"],
                        score=float(row.get("rank_score", row.get("importance", 0.0))),
                        confidence=float(row["confidence"]),
                        importance=float(row["importance"]),
                        sensitivity=row["sensitivity"],
                        validity=_memory_validity(row),
                        embedding_status=row.get("embedding_status", "pending"),
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
                        requires_user_confirmation=bool(
                            row.get("requires_user_confirmation", False)
                        ),
                        source=row["source"],
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
            groups.setdefault(item.kind, []).append(item)
        blocks: list[MemoryBlock] = []
        token_total = 0
        for kind, items in groups.items():
            title = _memory_block_title(kind)
            block_items: list[MemoryBlockItem] = []
            for item in items:
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
                        block_type=_block_type_for_kind(kind),
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
                include_sensitive=include_sensitive,
                include_asset_scoped=include_asset_scoped,
                asset_scope_ids=asset_scope_ids,
            )
            if reason:
                filtered.append(MemorySearchFilteredItem(memory_id=memory_id, reason=reason))
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
        source = {
            "type": "conversation" if conversation_id else "manual",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "message_id": message_id,
            "trace_id": trace_id,
        }
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
        )
        if result.blocked:
            response = "这条内容涉及敏感信息，我不会写入长期记忆。"
        elif command.kind == "block":
            response = "好的，这条不会写入长期记忆。"
        elif result.memories:
            response = "记住了。"
        elif result.candidates and result.candidates[0].decision == "discarded_duplicate":
            response = "这条我已经记过了，不会重复写入。"
        else:
            response = "我没有把这条写入长期记忆。"
        return MemoryCommandResult(
            handled=True,
            response_text=response,
            candidates=result.candidates,
            memories=result.memories,
            blocked=result.blocked,
            reason=result.reason,
        )

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
        classification = self._safety.classify_chat_input(text)
        summary = command.summary
        now = utc_now_iso()
        if classification.sensitivity_hits:
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
                score={"policy": "sensitive_block", "hits": classification.sensitivity_hits},
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
                    "sensitivity_hits": classification.sensitivity_hits,
                },
                trace_id=trace_id,
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
            return MemoryCommandResult(handled=True, candidates=[candidate], reason="blocked")

        score_span = await self._start_span(
            trace_id,
            TraceSpanType.MEMORY_SCORE,
            "score memory candidate",
            parent_span_id=root_span_id,
            metadata={"kind": command.memory_kind},
        )
        final_score = float(command.score)
        await self._end_span(
            score_span,
            output_data={"final_score": final_score, "threshold": MIN_WRITE_SCORE},
        )
        score = _score_decision(command)
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
                score={"base": final_score, "duplicate_memory_id": duplicate["memory_id"]},
                final_score=final_score,
                sensitivity="low",
                decision="discarded_duplicate",
                decision_reason="duplicate_active_memory",
                now=now,
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
            },
            final_score=final_score,
            sensitivity="low",
            decision=decision,
            decision_reason=score.reason,
            now=now,
        )
        if decision != "auto_written":
            return MemoryCommandResult(handled=True, candidates=[candidate], reason=decision)

        memory = await self._insert_memory_from_candidate(
            _candidate_row(candidate),
            decision="auto_written",
            trace_id=trace_id,
            now=now,
            supersede_query=command.supersede_query,
        )
        return MemoryCommandResult(handled=True, candidates=[candidate], memories=[memory])

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
            await self._end_span(
                conflict_span,
                output_data={
                    "superseded_memory_id": old_memory["memory_id"] if old_memory else None
                },
            )
        memory_id = new_id("mem")
        data = {
            "memory_id": memory_id,
            "organization_id": candidate["organization_id"],
            "member_id": candidate["member_id"],
            "user_id": candidate["user_id"],
            "layer": candidate["proposed_layer"],
            "kind": candidate["proposed_kind"],
            "scope_type": candidate["proposed_scope_type"],
            "scope_id": candidate["proposed_scope_id"],
            "summary_text": candidate["summary_text"],
            "payload": candidate["payload"],
            "source": candidate["source"],
            "confidence": candidate["final_score"],
            "importance": _importance_for_kind(candidate["proposed_kind"]),
            "sensitivity": candidate["sensitivity"],
            "valid_from": now,
            "valid_to": None,
            "supersedes": old_memory["memory_id"] if old_memory else None,
            "status": "active",
            "review_required": False,
            "embedding_status": "pending",
            "metadata": {"candidate_id": candidate["candidate_id"], "vector": "pending"},
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
                        "valid_to": now,
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
        await self._audit.write_event(
            actor_type="system",
            action=(
                "memory.correction_applied"
                if old_memory is not None
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
        await self._repo.update_memory_item(
            memory_id,
            {"status": status, "updated_at": now},
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
            elif any(stripped.startswith(marker) for marker in ("记住", "请记住")):
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
        scope_type=row["scope_type"],
        scope_id=row.get("scope_id"),
        summary_text=row["summary_text"],
        payload=row.get("payload", {}),
        source=row["source"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        sensitivity=row["sensitivity"],
        valid_from=row.get("valid_from"),
        valid_to=row.get("valid_to"),
        supersedes=row.get("supersedes"),
        status=row["status"],
        last_accessed_at=row.get("last_accessed_at"),
        access_count=int(row.get("access_count") or 0),
        review_required=bool(row.get("review_required", False)),
        embedding_status=row.get("embedding_status", "pending"),
        metadata=row.get("metadata", {}),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


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
        return False
    if request.layers and MemoryLayer(row["layer"]) not in request.layers:
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
        return True
    if row.get("member_id") == member_id or scope_id == member_id:
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
        return f"status_{status}"
    if row.get("valid_to") and str(row["valid_to"]) <= utc_now_iso():
        return "expired"
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
    if row.get("updated_at"):
        return 0.7
    if row.get("created_at"):
        return 0.55
    return 0.4


def _source_reliability(row: dict[str, Any]) -> float:
    source_value = row.get("source")
    source = source_value if isinstance(source_value, dict) else {}
    source_type = str(source.get("type") or "")
    if source_type in {"explicit_user", "user_confirmed", "manual"}:
        return 0.95
    if source_type in {"chat", "message", "turn"}:
        return 0.75
    return 0.65


def _explicitness_score(row: dict[str, Any]) -> float:
    metadata_value = row.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    source_value = row.get("source")
    source = source_value if isinstance(source_value, dict) else {}
    if metadata.get("explicit") is True or source.get("type") in {"explicit_user", "manual"}:
        return 0.95
    if row.get("kind") in {"preference", "project_fact", "correction"}:
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
    if row.get("status") == "superseded":
        notes.append("superseded_by_newer_memory")
    if row.get("valid_to") and str(row["valid_to"]) <= utc_now_iso():
        notes.append("expired_memory")
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
    if row.get("status") == "superseded" or row.get("supersedes"):
        return "superseded" if row.get("status") == "superseded" else "current"
    if row.get("valid_to") and str(row["valid_to"]) <= utc_now_iso():
        return "expired"
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


def _clean_summary(text: str) -> str:
    summary = text.strip()
    for prefix in ("请记住", "记住", "记住：", "记住:", "我的偏好是", "这个项目规则是"):
        summary = summary.replace(prefix, "", 1).strip()
    summary = summary.lstrip("：:，,。 ")
    return str(redact(summary or text.strip()))


def _parse_correction(text: str) -> dict[str, str] | None:
    match = re.search(r"不是(.+?)是(.+)", text)
    if match:
        old = match.group(1).strip(" ，,。:：")
        new = match.group(2).strip(" ，,。:：")
        return {"old": old, "summary": f"用户纠正：不是{old}，是{new}"}
    if "改成" in text:
        before, after = text.split("改成", 1)
        old = before.strip("把将 的偏好记忆，,。:：")
        new = after.strip(" ，,。:：")
        return {"old": old or before, "summary": f"用户将相关偏好改成：{new}"}
    if "以后不" in text:
        summary = _clean_summary(text)
        return {"old": summary, "summary": f"用户更新偏好：{summary}"}
    return None


def _is_explicit_memory_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return (
        any(marker in stripped for marker in BLOCK_MARKERS)
        or _parse_correction(stripped) is not None
        or _is_explicit_remember_command(stripped)
    )


def _is_explicit_remember_command(text: str) -> bool:
    stripped = text.strip()
    return any(stripped.startswith(marker) for marker in EXPLICIT_REMEMBER_PREFIXES)


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


def _score_decision(command: MemoryCommand) -> MemoryScore:
    if command.review_required:
        return MemoryScore(
            final_score=command.score,
            decision="needs_review",
            reason="review_required",
            review_required=True,
        )
    if command.score < MIN_WRITE_SCORE:
        return MemoryScore(
            final_score=command.score,
            decision="discarded_low_value",
            reason="score_below_threshold",
        )
    return MemoryScore(final_score=command.score, decision="auto_written", reason=None)


def _kind_for_summary(summary: str) -> str:
    if "项目" in summary or "规则" in summary or "核心层" in summary:
        return "project_fact"
    if "流程" in summary or "模板" in summary:
        return "skill_candidate"
    if "偏好" in summary or "以后" in summary or "喜欢" in summary or "文档" in summary:
        return "preference"
    return "semantic_note"


def _importance_for_kind(kind: str) -> float:
    if kind in {"preference", "project_fact", "correction"}:
        return 0.8
    if kind == "skill_candidate":
        return 0.65
    return 0.5


def _memory_block_title(kind: str) -> str:
    titles = {
        "preference": "用户偏好",
        "project_fact": "项目事实",
        "correction": "用户纠错",
        "skill_candidate": "流程候选",
    }
    return titles.get(kind, "相关记忆")


def _block_type_for_kind(kind: str) -> str:
    if kind == "correction":
        return "temporal"
    if kind == "skill_candidate":
        return "procedural"
    return "semantic"


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
    include_sensitive: bool,
    include_asset_scoped: bool,
    asset_scope_ids: list[str],
) -> str | None:
    if memory["status"] != "active":
        return f"status_{memory['status']}"
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
    return "".join(value.lower().split())


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
