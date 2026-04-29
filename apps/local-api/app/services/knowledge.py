from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from brain.adapters import estimate_text_tokens
from core_types import (
    ErrorCode,
    KnowledgeChunk,
    KnowledgeSearchHit,
    KnowledgeSource,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from safety_service import SafetyService
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.asset_repo import AssetRepository
from app.db.repositories.knowledge_repo import KnowledgeRepository
from app.db.repositories.retrieval_repo import RetrievalRepository
from app.schemas.knowledge import (
    KnowledgeAccessLogItem,
    KnowledgeIndexResponse,
    KnowledgeSearchApiResponse,
    KnowledgeSearchRequest,
    KnowledgeSourceCreateRequest,
)
from app.services.audit import AuditEventService
from app.services.capability import CapabilityGraphService, capability_request


@dataclass(frozen=True)
class KnowledgeRerankResult:
    rows: list[dict[str, Any]]
    suppressed: list[dict[str, Any]]
    latency_ms: float


class KnowledgeRerankService:
    scoring_policy = {
        "semantic_score": 0.4,
        "heading_match": 0.1,
        "source_trace": 0.1,
        "token_fit": 0.08,
        "sensitivity_penalty": 0.12,
        "untrusted_penalty": 0.08,
        "source_recency": 0.06,
        "asset_scope": 0.05,
        "provider_quality": 0.01,
    }

    def rerank(
        self,
        rows: list[dict[str, Any]],
        *,
        request: KnowledgeSearchRequest,
        limit: int,
    ) -> KnowledgeRerankResult:
        started = time.perf_counter()
        selected: list[dict[str, Any]] = []
        suppressed: list[dict[str, Any]] = []
        for row in rows:
            reason = _suppression_reason_for_chunk(row)
            base_score = _knowledge_semantic_score(row)
            if reason:
                suppressed.append(
                    _knowledge_suppressed_item(
                        target_id=row["chunk_id"],
                        reason=reason,
                        sensitivity=row.get("sensitivity"),
                        score=base_score,
                        metadata={
                            "retrieval_source": row.get("retrieval_source"),
                            "source_id": row.get("source_id"),
                        },
                    )
                )
                continue
            score_parts = {
                "semantic_score": base_score,
                "heading_match": _heading_score(row, request.query),
                "source_trace": _source_trace_score(row),
                "token_fit": _token_fit_score(row),
                "sensitivity_penalty": _knowledge_sensitivity_score(row),
                "untrusted_penalty": _untrusted_score(row),
                "source_recency": 0.7 if row.get("updated_at") else 0.5,
                "asset_scope": 0.9
                if not request.asset_id or row["asset_id"] == request.asset_id
                else 0.0,
                "provider_quality": _knowledge_provider_quality_score(row),
            }
            rerank_score = sum(
                score_parts[key] * weight for key, weight in self.scoring_policy.items()
            )
            untrusted = bool(row.get("metadata", {}).get("untrusted_external_content"))
            selected.append(
                {
                    **row,
                    "rerank_score": round(rerank_score, 4),
                    "selection_confidence": round(max(0.05, min(0.99, rerank_score)), 4),
                    "conflict_notes": ["untrusted_external_content"] if untrusted else [],
                    "requires_user_confirmation": untrusted and rerank_score < 0.45,
                    "untrusted_external_content": untrusted,
                    "selection_reason": [
                        *row.get("selection_reason", []),
                        "knowledge_rerank_quality_score",
                    ],
                }
            )
        selected.sort(
            key=lambda item: (
                float(item.get("rerank_score", 0.0)),
                float(item.get("rank_score", 0.0)),
            ),
            reverse=True,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        return KnowledgeRerankResult(
            rows=selected[:limit],
            suppressed=suppressed,
            latency_ms=latency_ms,
        )


class KnowledgeService:
    def __init__(
        self,
        *,
        repo: KnowledgeRepository,
        asset_repo: AssetRepository,
        capability: CapabilityGraphService,
        trace_service: TraceService,
        audit_service: AuditEventService,
        vector_service: Any | None = None,
        retrieval_repo: RetrievalRepository | None = None,
    ) -> None:
        self._repo = repo
        self._assets = asset_repo
        self._capability = capability
        self._trace = trace_service
        self._audit = audit_service
        self._vector = vector_service
        self._retrieval_repo = retrieval_repo
        self._reranker = KnowledgeRerankService()

    async def create_source(
        self,
        request: KnowledgeSourceCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeSource:
        asset = await self._assets.get_asset(request.asset_id)
        if asset is None or asset["asset_type"] != "knowledge_base":
            raise AppError(ErrorCode.ASSET_NOT_FOUND, "知识库资产不存在", status_code=404)
        source_id = new_id("ksrc")
        now = utc_now_iso()
        data = {
            "source_id": source_id,
            "organization_id": asset["organization_id"],
            "asset_id": request.asset_id,
            "source_type": request.source_type,
            "source_uri": request.source_uri,
            "display_name": request.display_name,
            "status": "active",
            "sensitivity": request.sensitivity,
            "content_hash": None,
            "last_scanned_at": None,
            "last_indexed_at": None,
            "metadata": redact(request.metadata),
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_source(data)
        await self._audit.write_event(
            actor_type="system",
            action="knowledge.source.added",
            object_type="knowledge_source",
            object_id=source_id,
            summary="知识库来源已添加",
            risk_level=RiskLevel.R1,
            payload={"source_id": source_id, "asset_id": request.asset_id},
            trace_id=trace_id,
        )
        source = await self._repo.get_source(source_id)
        if source is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "知识来源创建后无法读取", status_code=500)
        return KnowledgeSource(**source)

    async def index_source(
        self,
        source_id: str,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeIndexResponse:
        source = await self._repo.get_source(source_id)
        if source is None:
            raise AppError(
                ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND,
                "知识来源不存在",
                status_code=404,
            )
        asset = await self._assets.get_asset(source["asset_id"])
        if asset is None:
            raise AppError(ErrorCode.ASSET_NOT_FOUND, "知识库资产不存在", status_code=404)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.KNOWLEDGE_INDEX,
            "index knowledge source",
            metadata={"source_id": source_id, "asset_id": source["asset_id"]},
        )
        now = utc_now_iso()
        await self._repo.insert_index_job(
            {
                "job_id": new_id("kjob"),
                "organization_id": source["organization_id"],
                "asset_id": source["asset_id"],
                "source_id": source_id,
                "job_type": "index_source",
                "idempotency_key": f"knowledge.index:{source_id}",
                "status": "running",
                "created_at": now,
                "updated_at": now,
            }
        )
        try:
            raw = await _read_source_text(source, asset)
            privacy = SafetyService().classify_chat_input(raw)
            if privacy.sensitivity_hits:
                raise AppError(
                    ErrorCode.KNOWLEDGE_INDEX_FAILED,
                    "知识来源包含敏感内容，已拒绝索引",
                    status_code=422,
                    details={"sensitivity_hits": privacy.sensitivity_hits},
                )
            chunks = _chunk_text(raw)
            await self._repo.delete_chunks_for_source(source_id)
            seen_hashes: set[str] = set()
            written_count = 0
            for index, text in enumerate(chunks):
                chunk_id = new_id("kchk")
                content_hash = _hash_text(text)
                if content_hash in seen_hashes:
                    continue
                seen_hashes.add(content_hash)
                chunk_data = {
                    "chunk_id": chunk_id,
                    "organization_id": source["organization_id"],
                    "asset_id": source["asset_id"],
                    "source_id": source_id,
                    "chunk_index": index,
                    "content_text": redact(text),
                    "summary_text": _summary(text),
                    "token_estimate": estimate_text_tokens(text),
                    "sensitivity": source["sensitivity"],
                    "content_hash": content_hash,
                    "metadata": {
                        "heading_path": [],
                        "section_path": [],
                        "chunk_order": index,
                        "token_count": estimate_text_tokens(text),
                        "source_id": source_id,
                        "source_trace": {"source_id": source_id, "chunk_order": index},
                        "source_uri": source["source_uri"],
                        "content_hash": content_hash,
                        "untrusted_external_content": _is_external(source["source_uri"]),
                    },
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
                chunk_span = await self._start_span(
                    trace_id,
                    TraceSpanType.KNOWLEDGE_CHUNK,
                    "write knowledge chunk",
                    metadata={"chunk_id": chunk_id, "source_id": source_id},
                )
                await self._repo.insert_chunk(chunk_data)
                written_count += 1
                vector_ref_id = new_id("kvec")
                vector_provider = "none"
                vector_model = "fts_fallback"
                vector_status = "skipped"
                vector_error: str | None = "KNOWLEDGE_VECTOR_UNAVAILABLE"
                vector_id = chunk_id
                vector_collection = f"knowledge_{source['organization_id']}"
                if self._vector is not None:
                    try:
                        vector_result = await self._vector.upsert_text(
                            collection_name=f"knowledge_{source['organization_id']}",
                            target_type="knowledge_chunk",
                            target_id=chunk_id,
                            text=text,
                            organization_id=source["organization_id"],
                            metadata={
                                "asset_id": source["asset_id"],
                                "source_id": source_id,
                                "chunk_id": chunk_id,
                                "chunk_order": index,
                                "source_uri": source["source_uri"],
                                "untrusted_external_content": _is_external(
                                    source["source_uri"]
                                ),
                            },
                            content_hash=content_hash,
                            trace_id=trace_id,
                        )
                        vector_provider = str(vector_result.metadata.get("provider") or "local")
                        vector_model = str(
                            vector_result.metadata.get("embedding_model") or "local_hash_v1"
                        )
                        vector_status = "active"
                        vector_error = None
                        vector_id = (
                            vector_result.vector_ref_ids[0]
                            if vector_result.vector_ref_ids
                            else chunk_id
                        )
                        vector_collection = str(
                            vector_result.metadata.get("provider_collection_name")
                            or vector_collection
                        )
                        chunk_data["metadata"] = {
                            **chunk_data["metadata"],
                            "vector": {
                                "provider": vector_provider,
                                "provider_id": vector_result.metadata.get("provider_id"),
                                "model": vector_model,
                                "collection_name": vector_collection,
                                "fallback_chain": vector_result.metadata.get(
                                    "fallback_chain", []
                                ),
                                "degraded_reason": vector_result.metadata.get(
                                    "degraded_reason"
                                ),
                            },
                        }
                    except Exception:
                        vector_error = "KNOWLEDGE_VECTOR_UPSERT_FAILED"
                await self._repo.insert_vector_ref(
                    {
                        "vector_ref_id": vector_ref_id,
                        "organization_id": source["organization_id"],
                        "asset_id": source["asset_id"],
                        "source_id": source_id,
                        "chunk_id": chunk_id,
                        "collection_name": vector_collection,
                        "vector_id": vector_id,
                        "embedding_provider": vector_provider,
                        "embedding_model": vector_model,
                        "content_hash": content_hash,
                        "status": vector_status,
                        "last_synced_at": now if vector_status == "active" else None,
                        "error_code": vector_error,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                await self._end_span(
                    chunk_span,
                    output_data={
                        "chunk_id": chunk_id,
                        "vector_status": vector_status,
                        "provider": vector_provider,
                        "error_code": vector_error,
                    },
                )
            await self._repo.update_source(
                source_id,
                {
                    "status": "indexed",
                    "content_hash": _hash_text(raw),
                    "last_scanned_at": now,
                    "last_indexed_at": now,
                    "updated_at": now,
                },
            )
            await self._repo.insert_index_job(
                {
                    "job_id": new_id("kjob"),
                    "organization_id": source["organization_id"],
                    "asset_id": source["asset_id"],
                    "source_id": source_id,
                    "job_type": "index_source",
                    "idempotency_key": f"knowledge.index:{source_id}",
                    "status": "completed",
                    "created_at": now,
                    "updated_at": utc_now_iso(),
                }
            )
            await self._audit.write_event(
                actor_type="system",
                action="knowledge.index.completed",
                object_type="knowledge_source",
                object_id=source_id,
                summary="知识库索引完成",
                risk_level=RiskLevel.R1,
                payload={"source_id": source_id, "chunk_count": written_count},
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"chunk_count": written_count})
            updated = await self._repo.get_source(source_id)
            if updated is None:
                raise AppError(ErrorCode.INTERNAL_ERROR, "知识来源无法读取", status_code=500)
            return KnowledgeIndexResponse(
                source=KnowledgeSource(**updated),
                chunk_count=written_count,
                status="indexed",
            )
        except Exception as exc:
            await self._repo.insert_index_job(
                {
                    "job_id": new_id("kjob"),
                    "organization_id": source["organization_id"],
                    "asset_id": source["asset_id"],
                    "source_id": source_id,
                    "job_type": "index_source",
                    "idempotency_key": f"knowledge.index:{source_id}",
                    "status": "failed",
                    "error_code": ErrorCode.KNOWLEDGE_INDEX_FAILED.value,
                    "error_summary": str(redact(str(exc))),
                    "created_at": now,
                    "updated_at": utc_now_iso(),
                }
            )
            await self._repo.update_source(
                source_id,
                {"status": "failed", "updated_at": utc_now_iso()},
            )
            await self._audit.write_event(
                actor_type="system",
                action="knowledge.index.failed",
                object_type="knowledge_source",
                object_id=source_id,
                summary="知识库索引失败",
                risk_level=RiskLevel.R2,
                payload={
                    "source_id": source_id,
                    "error_code": ErrorCode.KNOWLEDGE_INDEX_FAILED.value,
                    "error_summary": str(redact(str(exc))),
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.KNOWLEDGE_INDEX_FAILED.value},
            )
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.KNOWLEDGE_INDEX_FAILED,
                "知识库索引失败",
                status_code=500,
            ) from exc

    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeSearchApiResponse:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.KNOWLEDGE_SEARCH,
            "search knowledge chunks",
            input_data={"query": request.query, "asset_id": request.asset_id},
        )
        try:
            rows: list[dict[str, Any]] = []
            retrieval_sources: set[str] = set()
            provider = "local" if self._vector is not None else None
            degraded_reason: str | None = None
            if self._vector is not None:
                vector_hits = await self._vector.search_text(
                    collection_name="knowledge_org_default",
                    target_type="knowledge_chunk",
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
                    row = await self._repo.get_chunk(str(hit["target_id"]))
                    if row is None or row.get("status") != "active":
                        continue
                    if request.asset_id and row["asset_id"] != request.asset_id:
                        continue
                    rows.append(
                        {
                            **row,
                            "rank_score": float(hit["score"]),
                            "retrieval_source": "semantic_vector",
                            "selection_reason": [
                                *hit.get("selection_reason", []),
                                "active_chunk",
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
                fts_rows = await self._repo.search_chunks(
                    organization_id="org_default",
                    asset_id=request.asset_id,
                    query=str(redact(request.query)),
                    limit=request.limit,
                )
                existing_ids = {row["chunk_id"] for row in rows}
                for row in fts_rows:
                    if row["chunk_id"] in existing_ids:
                        continue
                    source = "fts_fallback" if not retrieval_sources else "fts_supplement"
                    rows.append(
                        {
                            **row,
                            "retrieval_source": source,
                            "selection_reason": [
                                "fts_fallback" if source == "fts_fallback" else "fts_supplement",
                                "active_chunk",
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
            if rows and "semantic_vector" not in retrieval_sources and self._vector is not None:
                degraded_reason = "vector_hits_insufficient_fts_fallback"
                rows = [
                    {
                        **row,
                        "degraded_reason": row.get("degraded_reason") or degraded_reason,
                    }
                    for row in rows
                ]
            retrieval_id = new_id("kretr")
            rerank = self._reranker.rerank(rows, request=request, limit=request.limit)
            rows = rerank.rows
            selected: list[dict[str, Any]] = []
            filtered: list[str] = []
            suppressed = [*rerank.suppressed]
            access_id = new_id("kacc")
            for row in rows:
                decision = await self._capability.decide(
                    capability_request(
                        subject_type=request.subject_type,
                        subject_id=request.subject_id,
                        object_type="asset",
                        object_id=row["asset_id"],
                        action="read_knowledge",
                        context={
                            "conversation_id": request.conversation_id,
                            "task_id": request.task_id,
                            "query": redact(request.query),
                        },
                    ),
                    trace_id=trace_id,
                )
                if decision.allowed:
                    selected.append(row)
                else:
                    filtered.append(row["chunk_id"])
                    suppressed.append(
                        _knowledge_suppressed_item(
                            target_id=row["chunk_id"],
                            reason="capability_denied",
                            sensitivity=row.get("sensitivity"),
                            score=float(row.get("rerank_score", row.get("rank_score", 0.0))),
                            metadata={"asset_id": row["asset_id"], "source_id": row["source_id"]},
                        )
                    )
            now = utc_now_iso()
            if rows and not selected:
                first = rows[0]
                await self._repo.insert_access_log(
                    {
                        "access_id": access_id,
                        "organization_id": first["organization_id"],
                        "asset_id": first["asset_id"],
                        "source_id": first["source_id"],
                        "subject_type": request.subject_type,
                        "subject_id": request.subject_id,
                        "action": "read_knowledge",
                        "decision_id": None,
                        "trace_id": trace_id,
                        "query_hash": _hash_text(str(redact(request.query))),
                        "selected_chunk_ids": [],
                        "filtered_chunk_ids": filtered,
                        "reason": "capability_denied",
                        "created_at": now,
                    }
                )
                await self._persist_retrieval_quality(
                    retrieval_id=retrieval_id,
                    organization_id=first["organization_id"],
                    provider=provider,
                    selected=selected,
                    filtered=filtered,
                    rows=rows,
                    suppressed=suppressed,
                    retrieval_sources=retrieval_sources,
                    degraded=degraded_reason is not None
                    and "semantic_vector" not in retrieval_sources,
                    latency_ms=rerank.latency_ms,
                    request=request,
                    trace_id=trace_id,
                    created_at=now,
                )
                raise AppError(
                    ErrorCode.ASSET_ACCESS_DENIED,
                    "当前主体没有知识库读取授权",
                    status_code=403,
                )
            if selected:
                first = selected[0]
                await self._repo.insert_access_log(
                    {
                        "access_id": access_id,
                        "organization_id": first["organization_id"],
                        "asset_id": first["asset_id"],
                        "source_id": first["source_id"],
                        "subject_type": request.subject_type,
                        "subject_id": request.subject_id,
                        "action": "read_knowledge",
                        "decision_id": None,
                        "trace_id": trace_id,
                        "query_hash": _hash_text(str(redact(request.query))),
                        "selected_chunk_ids": [item["chunk_id"] for item in selected],
                        "filtered_chunk_ids": filtered,
                        "reason": "authorized",
                        "created_at": now,
                    }
                )
            await self._persist_retrieval_quality(
                retrieval_id=retrieval_id,
                organization_id=selected[0]["organization_id"] if selected else "org_default",
                provider=provider,
                selected=selected,
                filtered=filtered,
                rows=rows,
                suppressed=suppressed,
                retrieval_sources=retrieval_sources,
                degraded=degraded_reason is not None and "semantic_vector" not in retrieval_sources,
                latency_ms=rerank.latency_ms,
                request=request,
                trace_id=trace_id,
                created_at=now,
            )
            await self._end_span(
                span_id,
                output_data={
                    "retrieval_id": retrieval_id,
                    "selected_count": len(selected),
                    "filtered_count": len(filtered),
                    "suppressed_count": len(suppressed),
                },
            )
            return KnowledgeSearchApiResponse(
                retrieval_id=retrieval_id,
                items=[
                    KnowledgeSearchHit(
                        chunk_id=row["chunk_id"],
                        asset_id=row["asset_id"],
                        source_id=row["source_id"],
                        summary_text=row.get("summary_text"),
                        content_preview=_preview(row["content_text"]),
                        score=float(row.get("rank_score", 0.0)),
                        sensitivity=row["sensitivity"],
                        source_ref={
                            "source_id": row["source_id"],
                            "display_name": row.get("source_display_name"),
                            "untrusted_external_content": bool(
                                row.get("metadata", {}).get("untrusted_external_content")
                            ),
                            "selection_reason": row.get("selection_reason", []),
                        },
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
                        untrusted_external_content=bool(
                            row.get("untrusted_external_content", False)
                        ),
                    )
                    for row in selected
                ],
                selected_chunk_ids=[row["chunk_id"] for row in selected],
                filtered_chunk_ids=filtered,
                access_id=access_id if selected else None,
                degraded=degraded_reason is not None and "semantic_vector" not in retrieval_sources,
                provider=provider,
                degraded_reason=degraded_reason,
            )
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.KNOWLEDGE_SEARCH_FAILED)},
            )
            raise

    async def _persist_retrieval_quality(
        self,
        *,
        retrieval_id: str,
        organization_id: str,
        provider: str | None,
        selected: list[dict[str, Any]],
        filtered: list[str],
        rows: list[dict[str, Any]],
        suppressed: list[dict[str, Any]],
        retrieval_sources: set[str],
        degraded: bool,
        latency_ms: float,
        request: KnowledgeSearchRequest,
        trace_id: str | None,
        created_at: str,
    ) -> None:
        if self._retrieval_repo is None:
            return
        await self._retrieval_repo.insert_knowledge_retrieval_log(
            {
                "retrieval_id": retrieval_id,
                "organization_id": organization_id,
                "trace_id": trace_id,
                "conversation_id": request.conversation_id,
                "task_id": request.task_id,
                "subject_type": request.subject_type,
                "subject_id": request.subject_id,
                "asset_id": request.asset_id,
                "query_text_hash": _hash_text(str(redact(request.query))),
                "selected_chunk_ids": [item["chunk_id"] for item in selected],
                "filtered_chunk_ids": filtered,
                "ranking": [
                    {
                        "chunk_id": row["chunk_id"],
                        "score": float(row.get("rerank_score", row.get("rank_score", 0.0))),
                        "reason_codes": row.get("selection_reason", []),
                    }
                    for row in rows
                ],
                "retrieval_sources": sorted(retrieval_sources),
                "degraded": degraded,
                "created_at": created_at,
            }
        )
        await self._retrieval_repo.insert_rerank_run(
            {
                "rerank_run_id": new_id("rrank"),
                "retrieval_id": retrieval_id,
                "organization_id": organization_id,
                "target_type": "knowledge",
                "provider": provider,
                "scoring_policy": self._reranker.scoring_policy,
                "input_count": len(rows) + len(suppressed),
                "selected_count": len(selected),
                "suppressed_count": len(suppressed),
                "fallback_used": "semantic_vector" not in retrieval_sources,
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
                    "target_type": "knowledge",
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
                "target_type": "knowledge",
                "retrieval_id": retrieval_id,
                "summary": {
                    "selected_count": len(selected),
                    "suppressed_count": len(suppressed),
                    "retrieval_sources": sorted(retrieval_sources),
                },
                "metrics": {
                    "latency_ms": round(latency_ms, 4),
                    "fallback_used": "semantic_vector" not in retrieval_sources,
                    "precision_smoke": 1.0 if selected else 0.0,
                },
                "status": "completed",
                "trace_id": trace_id,
                "created_at": created_at,
            }
        )

    async def get_chunk(
        self,
        chunk_id: str,
        *,
        subject_type: str = "member",
        subject_id: str = "mem_xiaoyao",
        conversation_id: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> KnowledgeChunk:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.KNOWLEDGE_SEARCH,
            "get knowledge chunk",
            metadata={"chunk_id": chunk_id},
        )
        try:
            row = await self._repo.get_chunk(chunk_id)
            if row is None or row["status"] != "active":
                raise AppError(
                    ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND,
                    "知识片段不存在",
                    status_code=404,
                )
            decision = await self._capability.decide(
                capability_request(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    object_type="asset",
                    object_id=row["asset_id"],
                    action="read_knowledge",
                    context={
                        "conversation_id": conversation_id,
                        "task_id": task_id,
                        "chunk_id": chunk_id,
                    },
                ),
                trace_id=trace_id,
            )
            access_id = new_id("kacc")
            await self._repo.insert_access_log(
                {
                    "access_id": access_id,
                    "organization_id": row["organization_id"],
                    "asset_id": row["asset_id"],
                    "source_id": row["source_id"],
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "action": "get_chunk",
                    "decision_id": decision.decision_id,
                    "trace_id": trace_id,
                    "query_hash": _hash_text(chunk_id),
                    "selected_chunk_ids": [chunk_id] if decision.allowed else [],
                    "filtered_chunk_ids": [] if decision.allowed else [chunk_id],
                    "reason": "authorized" if decision.allowed else "capability_denied",
                    "created_at": utc_now_iso(),
                }
            )
            if not decision.allowed:
                raise AppError(
                    ErrorCode.ASSET_ACCESS_DENIED,
                    "当前主体没有知识库读取授权",
                    status_code=403,
                )
            await self._end_span(span_id, output_data={"chunk_id": chunk_id})
            return KnowledgeChunk(**row)
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.KNOWLEDGE_SEARCH_FAILED)},
            )
            raise

    async def list_access_logs(self, limit: int = 50) -> list[KnowledgeAccessLogItem]:
        return [KnowledgeAccessLogItem(**row) for row in await self._repo.list_access_logs(limit)]

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            input_data=input_data,
            metadata=metadata,
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(span_id, status=status, output_data=output_data)


async def _read_source_text(source: dict[str, Any], asset: dict[str, Any]) -> str:
    source_uri = source["source_uri"]
    source_type = source["source_type"]
    if source_uri.startswith("http://") or source_uri.startswith("https://"):
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(source_uri)
            response.raise_for_status()
            return response.text
    root_uri = str(asset["config"].get("root_uri") or "")
    root = Path(root_uri).resolve()
    path = Path(source_uri).resolve()
    _reject_sensitive_source_path(path)
    if root not in [path, *path.parents]:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "知识来源路径必须位于知识库 root_uri 内",
            status_code=422,
        )
    if source_type == "folder":
        texts = []
        for child in sorted(path.rglob("*")):
            if child.suffix.lower() in {".md", ".markdown", ".txt"} and child.is_file():
                _reject_sensitive_source_path(child)
                texts.append(child.read_text(encoding="utf-8"))
        return "\n\n".join(texts)
    if source_type in {"markdown", "txt", "note"}:
        return path.read_text(encoding="utf-8")
    if source_type == "pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:  # pragma: no cover - depends on optional parser package
            raise AppError(
                ErrorCode.KNOWLEDGE_INDEX_FAILED,
                "当前环境缺少 pypdf，无法解析 PDF 文本层",
                status_code=500,
            ) from exc
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise AppError(ErrorCode.VALIDATION_ERROR, "不支持的知识来源类型", status_code=422)


def _reject_sensitive_source_path(path: Path) -> None:
    lowered_parts = {part.lower() for part in path.parts}
    sensitive_names = {"master.key", "local_secrets.json", ".env", ".env.local"}
    if "secrets" in lowered_parts or path.name.lower() in sensitive_names:
        raise AppError(
            ErrorCode.KNOWLEDGE_INDEX_FAILED,
            "知识来源路径指向敏感存储，已拒绝索引",
            status_code=422,
        )


def _chunk_text(text: str, *, max_chars: int = 1200) -> list[str]:
    clean = str(redact(text)).strip()
    if not clean:
        return []
    return [clean[index : index + max_chars] for index in range(0, len(clean), max_chars)]


def _summary(text: str) -> str:
    return _preview(text, limit=180)


def _preview(text: str, *, limit: int = 360) -> str:
    clean = " ".join(str(redact(text)).split())
    return clean[:limit]


def _suppression_reason_for_chunk(row: dict[str, Any]) -> str | None:
    status = str(row.get("status") or "")
    if status != "active":
        return f"status_{status or 'unknown'}"
    sensitivity = str(row.get("sensitivity") or "low")
    if sensitivity in {"high", "secret", "credential", "wallet"}:
        return f"sensitivity_{sensitivity}"
    return None


def _knowledge_semantic_score(row: dict[str, Any]) -> float:
    source = str(row.get("retrieval_source") or "")
    raw = float(row.get("rank_score", 0.0) or 0.0)
    if source == "semantic_vector":
        return max(0.0, min(1.0, raw))
    if source == "fts_supplement":
        return max(0.15, min(0.7, raw if raw <= 1 else 0.6))
    if source == "fts_fallback":
        return max(0.1, min(0.6, raw if raw <= 1 else 0.5))
    return max(0.05, min(0.45, raw))


def _heading_score(row: dict[str, Any], query: str) -> float:
    metadata_value = row.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    heading_path = " ".join(str(item) for item in metadata.get("heading_path", []))
    if not heading_path:
        return 0.45
    terms = {term.lower() for term in query.split() if term.strip()}
    return 0.85 if any(term in heading_path.lower() for term in terms) else 0.55


def _source_trace_score(row: dict[str, Any]) -> float:
    metadata_value = row.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    return 0.9 if metadata.get("source_id") and metadata.get("content_hash") else 0.55


def _token_fit_score(row: dict[str, Any]) -> float:
    token_count = int(row.get("token_estimate") or 0)
    if token_count <= 0:
        return 0.5
    if token_count <= 1200:
        return 0.9
    if token_count <= 2400:
        return 0.65
    return 0.35


def _knowledge_sensitivity_score(row: dict[str, Any]) -> float:
    sensitivity = str(row.get("sensitivity") or "low")
    if sensitivity in {"secret", "credential", "wallet"}:
        return 0.0
    if sensitivity == "high":
        return 0.15
    if sensitivity == "medium":
        return 0.6
    return 0.9


def _knowledge_provider_quality_score(row: dict[str, Any]) -> float:
    provider = str(row.get("provider") or "")
    model = str(row.get("embedding_model") or "")
    if "local_hash" in model or provider == "local":
        return 0.55
    if provider in {"local_model", "chroma", "external_compatible"}:
        return 0.85
    return 0.6


def _untrusted_score(row: dict[str, Any]) -> float:
    metadata_value = row.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    return 0.55 if metadata.get("untrusted_external_content") else 0.85


def _knowledge_suppressed_item(
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


def _hash_text(text: str) -> str:
    return hashlib.sha256(str(redact(text)).encode("utf-8")).hexdigest()


def _is_external(uri: str) -> bool:
    return uri.startswith("http://") or uri.startswith("https://")
