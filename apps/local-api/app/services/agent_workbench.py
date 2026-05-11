from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from brain.adapters import estimate_text_tokens
from core_types import ErrorCode, RiskLevel, TraceSpanStatus, TraceSpanType, WorkbenchContext
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.agent_workbench_repo import AgentWorkbenchRepository
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.memory_repo import MemoryRepository
from app.schemas.agent_workbench import (
    AgentContextFileDiff,
    AgentContextFileReplay,
    AgentContextFileVersion,
    AgentWorkbenchContextPack,
    AgentWorkbenchJobItem,
)
from app.schemas.skills import SkillGrowthCandidateConsolidateRequest
from app.services.audit import AuditEventService
from app.services.memory import MemoryService

WORKER_ID = "agent_workbench_reflection_worker"
JOB_STALE_AFTER_MINUTES = 15


class AgentWorkbenchService:
    def __init__(
        self,
        *,
        repo: AgentWorkbenchRepository,
        chat_repo: ChatRepository,
        member_repo: MemberRepository,
        memory_repo: MemoryRepository,
        memory_service: MemoryService,
        artifact_root: Path,
        trace_service: TraceService,
        audit_service: AuditEventService,
        skill_plugin_service: Any | None = None,
        skill_repository_service: Any | None = None,
    ) -> None:
        self._repo = repo
        self._chat = chat_repo
        self._members = member_repo
        self._memory_repo = memory_repo
        self._memory = memory_service
        self._artifact_root = artifact_root
        self._trace = trace_service
        self._audit = audit_service
        self._skill_plugins = skill_plugin_service
        self._skill_repositories = skill_repository_service

    async def enqueue_reflect_after_turn(self, turn_id: str) -> AgentWorkbenchJobItem | None:
        turn = await self._chat.get_turn(turn_id)
        if turn is None or turn["status"] != "completed":
            return None
        member = await self._members.get_member(turn["member_id"])
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        now = utc_now_iso()
        idempotency_key = f"agent_workbench.reflect_after_turn:{turn_id}"
        await self._repo.insert_job(
            {
                "job_id": new_id("wbjob"),
                "organization_id": member["organization_id"],
                "turn_id": turn_id,
                "idempotency_key": idempotency_key,
                "job_type": "reflect_after_turn",
                "status": "pending",
                "payload": {
                    "member_id": turn["member_id"],
                    "conversation_id": turn["conversation_id"],
                    "user_message_id": turn["user_message_id"],
                    "assistant_message_id": turn.get("assistant_message_id"),
                    "trace_id": turn.get("trace_id"),
                },
                "trace_id": turn.get("trace_id"),
                "created_at": now,
                "updated_at": now,
            }
        )
        job = await self._repo.get_job_by_idempotency_key(idempotency_key)
        return AgentWorkbenchJobItem(**job) if job else None

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
    ) -> list[AgentWorkbenchJobItem]:
        rows = await self._repo.list_jobs(
            status=status,
            job_type=job_type,
            limit=max(1, min(limit, 200)),
        )
        return [AgentWorkbenchJobItem(**redact(row)) for row in rows]

    async def recover_stale_jobs(self) -> int:
        stale_before = (utc_now() - timedelta(minutes=JOB_STALE_AFTER_MINUTES)).isoformat()
        return await self._repo.restore_stale_jobs(
            stale_before=stale_before,
            updated_at=utc_now_iso(),
        )

    async def process_pending_jobs(
        self,
        *,
        limit: int = 10,
        trace_id: str | None = None,
    ) -> int:
        processed = 0
        for _ in range(max(1, min(limit, 50))):
            job = await self._repo.claim_next_job(worker_id=WORKER_ID, now=utc_now_iso())
            if job is None:
                break
            await self._execute_job(job, trace_id=trace_id)
            processed += 1
        return processed

    async def build_context_pack(
        self,
        *,
        member_id: str,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        trace_id: str | None = None,
        context_pack_id: str | None = None,
        context_file_refs: list[dict[str, Any]] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        persist: bool = False,
    ) -> AgentWorkbenchContextPack:
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        now = utc_now_iso()
        memory_refs = await self._active_memory_refs(member_id=member_id)
        skill_refs = await self._enabled_skill_refs(member_id=member_id)
        latest_file = await self._repo.latest_context_file_version(
            member_id=member_id,
            conversation_id=conversation_id,
        )
        existing_context_refs: list[dict[str, Any]] = []
        if latest_file is not None:
            existing_context_refs.append(_context_file_ref(latest_file))
        if context_file_refs:
            existing_context_refs = [*context_file_refs, *existing_context_refs]
        existing_context_refs = _dedupe_refs(existing_context_refs, key="version_id")
        working_state = (
            await self._chat.get_working_state(conversation_id)
            if conversation_id is not None
            else None
        )
        summary = _pack_summary(
            memory_refs=memory_refs,
            skill_refs=skill_refs,
            context_file_refs=existing_context_refs,
            working_state=working_state,
        )
        pack_data = {
            "context_pack_id": context_pack_id or new_id("wbctx"),
            "organization_id": member["organization_id"],
            "member_id": member_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "summary_text": summary,
            "memory_refs": memory_refs,
            "skill_refs": skill_refs,
            "context_file_refs": existing_context_refs[:6],
            "working_state": redact(working_state or {}),
            "source_refs": redact(source_refs or []),
            "token_estimate": estimate_text_tokens(
                json.dumps(
                    {
                        "summary": summary,
                        "memory_refs": memory_refs,
                        "skill_refs": skill_refs,
                        "context_file_refs": existing_context_refs[:6],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            "status": "active",
            "trace_id": trace_id,
            "created_at": now,
        }
        if persist:
            await self._repo.insert_context_pack(pack_data)
        return AgentWorkbenchContextPack(**pack_data)

    async def latest_context_pack(
        self,
        *,
        member_id: str,
        conversation_id: str | None = None,
    ) -> AgentWorkbenchContextPack | None:
        row = await self._repo.latest_context_pack(
            member_id=member_id,
            conversation_id=conversation_id,
        )
        return AgentWorkbenchContextPack(**row) if row else None

    async def latest_workbench_context(
        self,
        *,
        member_id: str,
        conversation_id: str | None = None,
    ) -> WorkbenchContext | None:
        pack = await self.latest_context_pack(
            member_id=member_id,
            conversation_id=conversation_id,
        )
        if pack is not None:
            return pack.as_workbench_context()
        latest_file = await self._repo.latest_context_file_version(
            member_id=member_id,
            conversation_id=conversation_id,
        )
        if latest_file is None:
            return None
        return WorkbenchContext(
            context_file_version_id=latest_file["version_id"],
            summary=latest_file["summary_text"],
            memory_refs=latest_file["memory_refs"],
            skill_refs=latest_file["skill_refs"],
            context_file_refs=[_context_file_ref(latest_file)],
            source_refs=latest_file["source_refs"],
            generated_at=latest_file["created_at"],
        )

    async def reflect_turn(
        self,
        turn_id: str,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        turn = await self._chat.get_turn(turn_id)
        if turn is None:
            raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
        if turn["status"] != "completed":
            raise AppError(ErrorCode.CONFLICT, "只能反思已完成 turn", status_code=409)
        effective_trace_id = trace_id or turn.get("trace_id")
        span_id = await self._start_span(
            effective_trace_id,
            "reflect agent workbench turn",
            input_data={"turn_id": turn_id},
            metadata={"member_id": turn["member_id"]},
        )
        try:
            user_message = await self._chat.get_message(turn["user_message_id"])
            assistant_message = (
                await self._chat.get_message(turn["assistant_message_id"])
                if turn.get("assistant_message_id")
                else None
            )
            user_text = str(user_message.get("content_text") if user_message else "")
            assistant_text = str(assistant_message.get("content_text") if assistant_message else "")
            memory_result = await self._memory.extract_from_text(
                user_text,
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                turn_id=turn_id,
                message_id=turn["user_message_id"],
                trace_id=effective_trace_id,
                root_span_id=span_id,
                allow_implicit=True,
                create_job=False,
            )
            experience_summary = _experience_summary(
                user_text=user_text,
                assistant_text=assistant_text,
                turn_id=turn_id,
            )
            experience_result = await self._memory.consolidate_experience(
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                outcome="completed",
                summary_text=experience_summary,
                source={
                    "type": "conversation_turn",
                    "turn_id": turn_id,
                    "message_id": turn.get("user_message_id"),
                    "trace_id": effective_trace_id,
                    "channel": "local",
                },
                evidence={
                    "assistant_message_id": turn.get("assistant_message_id"),
                    "memory_candidates": [
                        item.candidate_id for item in memory_result.candidates
                    ],
                    "memory_ids": [item.memory_id for item in memory_result.memories],
                    "reflection_policy": "candidate_or_existing_safe_policy_only",
                },
                steps=[
                    {"step_type": "memory_extract", "status": "completed"},
                    {"step_type": "experience_consolidate", "status": "completed"},
                    {"step_type": "context_file_version", "status": "planned"},
                ],
                trace_id=effective_trace_id,
                root_span_id=span_id,
            )
            skill_growth = []
            if self._skill_repositories is not None:
                skill_growth = await self._skill_repositories.consolidate_growth_candidates(
                    SkillGrowthCandidateConsolidateRequest(
                        member_id=turn["member_id"],
                        experience_id=experience_result.experience.experience_id,
                        limit=20,
                    ),
                    trace_id=effective_trace_id,
                )
            context_pack_id = new_id("wbctx")
            source_refs = [
                {
                    "type": "conversation_turn",
                    "turn_id": turn_id,
                    "conversation_id": turn["conversation_id"],
                    "user_message_id": turn["user_message_id"],
                    "assistant_message_id": turn.get("assistant_message_id"),
                    "trace_id": effective_trace_id,
                },
                {
                    "type": "memory_experience",
                    "experience_id": experience_result.experience.experience_id,
                    "memory_id": experience_result.experience.memory_id,
                },
                *[
                    {
                        "type": "skill_growth_evidence",
                        "evidence_id": item.evidence_id,
                        "candidate_id": item.candidate_id,
                        "decision": item.decision,
                    }
                    for item in skill_growth
                ],
            ]
            pre_pack = await self.build_context_pack(
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                turn_id=turn_id,
                trace_id=effective_trace_id,
                context_pack_id=context_pack_id,
                source_refs=source_refs,
                persist=False,
            )
            context_file = await self._write_context_file_version(
                pack=pre_pack,
                source_turn_id=turn_id,
                source_trace_id=effective_trace_id,
                context_pack_id=context_pack_id,
            )
            context_pack = await self.build_context_pack(
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                turn_id=turn_id,
                trace_id=effective_trace_id,
                context_pack_id=context_pack_id,
                context_file_refs=[_context_file_ref(context_file.model_dump(mode="json"))],
                source_refs=source_refs,
                persist=True,
            )
            await self._repo.link_context_file_pack(
                version_id=context_file.version_id,
                context_pack_id=context_pack.context_pack_id,
                updated_at=utc_now_iso(),
            )
            result = {
                "turn_id": turn_id,
                "memory_candidates": [item.candidate_id for item in memory_result.candidates],
                "memory_ids": [item.memory_id for item in memory_result.memories],
                "experience_id": experience_result.experience.experience_id,
                "skill_growth_evidence_ids": [item.evidence_id for item in skill_growth],
                "context_pack_id": context_pack.context_pack_id,
                "context_file_version_id": context_file.version_id,
            }
            await self._audit.write_event(
                actor_type="system",
                action="agent_workbench.turn_reflected",
                object_type="chat_turn",
                object_id=turn_id,
                summary="Agent workbench turn reflection completed",
                risk_level=RiskLevel.R1,
                payload=redact(result),
                trace_id=effective_trace_id,
            )
            await self._end_span(span_id, output_data=result)
            return result
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def list_context_files(
        self,
        *,
        member_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentContextFileVersion]:
        rows = await self._repo.list_context_file_versions(
            member_id=member_id,
            conversation_id=conversation_id,
            limit=max(1, min(limit, 200)),
        )
        return [AgentContextFileVersion(**row) for row in rows]

    async def get_context_file_version(self, version_id: str) -> AgentContextFileVersion:
        row = await self._repo.get_context_file_version(version_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "上下文文件版本不存在", status_code=404)
        return AgentContextFileVersion(**row)

    async def replay_context_file(self, version_id: str) -> AgentContextFileReplay:
        version = await self.get_context_file_version(version_id)
        path = self._artifact_path_from_uri(version.artifact_uri)
        artifact_exists = path.exists()
        checksum_matches = False
        preview: dict[str, Any] = {}
        if artifact_exists:
            raw = path.read_bytes()
            checksum_matches = _checksum(raw) == version.artifact_checksum
            try:
                preview = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                preview = {"decode_status": "failed"}
        return AgentContextFileReplay(
            version=version,
            artifact_exists=artifact_exists,
            checksum_matches=checksum_matches,
            artifact_preview=redact(preview),
            source_refs=version.source_refs,
            memory_refs=version.memory_refs,
            skill_refs=version.skill_refs,
        )

    async def diff_context_files(
        self,
        *,
        from_version_id: str,
        to_version_id: str,
    ) -> AgentContextFileDiff:
        from_version = await self.get_context_file_version(from_version_id)
        to_version = await self.get_context_file_version(to_version_id)
        return AgentContextFileDiff(
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            summary_changed=from_version.summary_text != to_version.summary_text,
            artifact_checksum_changed=(
                from_version.artifact_checksum != to_version.artifact_checksum
            ),
            added_memory_refs=_ref_delta(
                from_version.memory_refs,
                to_version.memory_refs,
                key="memory_id",
            )[0],
            removed_memory_refs=_ref_delta(
                from_version.memory_refs,
                to_version.memory_refs,
                key="memory_id",
            )[1],
            added_skill_refs=_ref_delta(
                from_version.skill_refs,
                to_version.skill_refs,
                key="skill_id",
            )[0],
            removed_skill_refs=_ref_delta(
                from_version.skill_refs,
                to_version.skill_refs,
                key="skill_id",
            )[1],
            source_ref_delta={
                "from_count": len(from_version.source_refs),
                "to_count": len(to_version.source_refs),
            },
        )

    async def _execute_job(self, job: dict[str, Any], *, trace_id: str | None) -> None:
        now = utc_now_iso()
        try:
            if job["job_type"] != "reflect_after_turn":
                await self._repo.update_job_status(
                    idempotency_key=job["idempotency_key"],
                    status="failed",
                    error_code=ErrorCode.INTERNAL_ERROR.value,
                    error_message=f"unsupported workbench job type: {job['job_type']}",
                    updated_at=now,
                    trace_id=trace_id,
                )
                return
            await self.reflect_turn(str(job["turn_id"]), trace_id=trace_id or job.get("trace_id"))
            completed_at = utc_now_iso()
            await self._repo.update_job_status(
                idempotency_key=job["idempotency_key"],
                status="completed",
                error_code=None,
                error_message=None,
                updated_at=completed_at,
                completed_at=completed_at,
                trace_id=trace_id,
            )
        except Exception as exc:
            attempts = int(job.get("attempts") or 0)
            max_attempts = int(job.get("max_attempts") or 3)
            terminal = attempts >= max_attempts
            await self._repo.update_job_status(
                idempotency_key=job["idempotency_key"],
                status="failed" if terminal else "pending",
                error_code=exc.__class__.__name__,
                error_message=str(redact(str(exc))),
                updated_at=utc_now_iso(),
                trace_id=trace_id,
            )

    async def _active_memory_refs(self, *, member_id: str) -> list[dict[str, Any]]:
        rows = await self._memory_repo.list_memory_items(
            member_id=member_id,
            status="active",
            limit=8,
        )
        refs: list[dict[str, Any]] = []
        for row in rows:
            source = row.get("source", {})
            safe_source = redact(source if isinstance(source, dict) else {})
            safe_source = safe_source if isinstance(safe_source, dict) else {}
            source_turn_id = (
                safe_source.get("turn_id")
                or safe_source.get("source_turn_id")
                or row.get("source_turn_id")
            )
            sensitivity = str(
                row.get("sensitivity")
                or safe_source.get("sensitivity")
                or "low"
            )
            selection_reason = [
                item
                for item in [
                    "agent_workbench_active_memory",
                    row.get("layer"),
                    row.get("kind"),
                ]
                if item
            ]
            refs.append(
                {
                    "memory_id": row["memory_id"],
                    "layer": row.get("layer"),
                    "kind": row.get("kind"),
                    "summary": str(redact(row.get("summary_text") or ""))[:240],
                    "confidence": row.get("confidence"),
                    "quality_score": row.get("quality_score", 0.5),
                    "sensitivity": sensitivity,
                    "source_turn_id": source_turn_id,
                    "selection_reason": selection_reason,
                    "source": safe_source,
                }
            )
        return refs

    async def _enabled_skill_refs(self, *, member_id: str) -> list[dict[str, Any]]:
        del member_id
        if self._skill_plugins is None:
            return []
        try:
            skills = await self._skill_plugins.list_skills(status="enabled")
        except Exception:
            return []
        refs: list[dict[str, Any]] = []
        for skill in skills[:8]:
            data = skill.model_dump(mode="json") if hasattr(skill, "model_dump") else dict(skill)
            bundle_data: dict[str, Any] = {}
            bundle_id = data.get("bundle_id")
            if bundle_id:
                try:
                    bundle = await self._skill_plugins.get_bundle(str(bundle_id))
                    bundle_data = (
                        bundle.model_dump(mode="json")
                        if hasattr(bundle, "model_dump")
                        else dict(bundle)
                    )
                except Exception:
                    bundle_data = {}
            trust_level = str(
                bundle_data.get("trust_level")
                or data.get("trust_level")
                or "unknown"
            )
            required_assets = list(data.get("required_assets") or [])
            source_type = str(bundle_data.get("source_type") or "skill_registry")
            requires_safety = True
            refs.append(
                {
                    "skill_id": data["skill_id"],
                    "bundle_id": bundle_id,
                    "display_name": data.get("display_name") or data.get("name"),
                    "description": str(redact(data.get("description") or ""))[:240],
                    "source": {
                        "type": "skill_registry",
                        "source_type": source_type,
                        "bundle_id": bundle_id,
                        "signature_status": bundle_data.get("signature_status") or "unknown",
                    },
                    "trust_level": trust_level,
                    "requires_asset_broker": bool(required_assets),
                    "requires_safety": requires_safety,
                    "status": data.get("status"),
                    "use_boundary": "must_execute_through_tool_runtime_safety_approval",
                }
            )
        return refs

    async def _write_context_file_version(
        self,
        *,
        pack: AgentWorkbenchContextPack,
        source_turn_id: str,
        source_trace_id: str | None,
        context_pack_id: str,
    ) -> AgentContextFileVersion:
        context_file_key = _context_file_key(pack.member_id, pack.conversation_id)
        previous = await self._repo.latest_context_file_version(
            member_id=pack.member_id,
            conversation_id=pack.conversation_id,
        )
        version_index = await self._repo.next_context_file_version_index(context_file_key)
        version_id = new_id("ctxfile")
        now = utc_now_iso()
        artifact_payload = redact(
            {
                "version_id": version_id,
                "context_pack_id": context_pack_id,
                "member_id": pack.member_id,
                "conversation_id": pack.conversation_id,
                "summary_text": pack.summary_text,
                "memory_refs": pack.memory_refs,
                "skill_refs": pack.skill_refs,
                "context_file_refs": pack.context_file_refs,
                "source_refs": pack.source_refs,
                "token_estimate": pack.token_estimate,
                "source_turn_id": source_turn_id,
                "source_trace_id": source_trace_id,
                "created_at": now,
            }
        )
        raw = json.dumps(
            artifact_payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        rel_path = Path("agent-workbench") / _safe_path_part(pack.member_id) / (
            _safe_path_part(pack.conversation_id or "member")
        ) / f"{version_id}.json"
        path = (self._artifact_root / rel_path).resolve()
        if self._artifact_root.resolve() not in [path, *path.parents]:
            raise AppError(ErrorCode.ARTIFACT_WRITE_FAILED, "workbench artifact 路径不合法")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        uri = "artifact://agent-workbench/" + "/".join(rel_path.parts[1:])
        row = {
            "version_id": version_id,
            "organization_id": pack.organization_id,
            "member_id": pack.member_id,
            "conversation_id": pack.conversation_id,
            "context_file_key": context_file_key,
            "version_index": version_index,
            "status": "active",
            "summary_text": pack.summary_text,
            "artifact_uri": uri,
            "artifact_checksum": _checksum(raw),
            "artifact_size_bytes": len(raw),
            "source_turn_id": source_turn_id,
            "source_trace_id": source_trace_id,
            "context_pack_id": None,
            "diff_base_version_id": previous["version_id"] if previous else None,
            "source_refs": pack.source_refs,
            "memory_refs": pack.memory_refs,
            "skill_refs": pack.skill_refs,
            "metadata": {
                "artifact_policy": "db_index_plus_redacted_artifact_file",
                "source_of_truth": "workbench_snapshot_not_business_record",
                "redaction": "trace_service.redact",
            },
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_context_file_version(row)
        await self._audit.write_event(
            actor_type="system",
            action="agent_workbench.context_file_version.created",
            object_type="agent_context_file_version",
            object_id=version_id,
            summary="Agent workbench context file version created",
            risk_level=RiskLevel.R1,
            payload={
                "version_id": version_id,
                "artifact_uri": uri,
                "artifact_checksum": row["artifact_checksum"],
            },
            trace_id=source_trace_id,
        )
        return AgentContextFileVersion(**row)

    def _artifact_path_from_uri(self, uri: str) -> Path:
        prefix = "artifact://agent-workbench/"
        if not uri.startswith(prefix):
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "不支持的 workbench artifact URI")
        relative = Path(*uri.removeprefix(prefix).split("/"))
        path = (self._artifact_root / "agent-workbench" / relative).resolve()
        if self._artifact_root.resolve() not in [path, *path.parents]:
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "workbench artifact URI 不合法")
        return path

    async def _start_span(
        self,
        trace_id: str | None,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MEMORY_WRITE,
            name=name,
            input_data=redact(input_data or {}),
            metadata=redact(metadata or {}),
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is None:
            return
        await self._trace.end_span(
            span_id,
            status=status,
            output_data=redact(output_data or {}),
        )


def _pack_summary(
    *,
    memory_refs: list[dict[str, Any]],
    skill_refs: list[dict[str, Any]],
    context_file_refs: list[dict[str, Any]],
    working_state: dict[str, Any] | None,
) -> str:
    parts = [
        f"稳定记忆 {len(memory_refs)} 条",
        f"可用 Skill {len(skill_refs)} 个",
        f"上下文文件 {len(context_file_refs)} 个版本",
    ]
    if working_state and working_state.get("active_topic"):
        parts.append(f"当前主题：{redact(working_state['active_topic'])}")
    if working_state and working_state.get("user_goal"):
        parts.append(f"用户目标：{redact(working_state['user_goal'])}")
    if memory_refs:
        parts.append("记忆要点：" + "；".join(str(item.get("summary") or "") for item in memory_refs[:3]))
    if skill_refs:
        parts.append(
            "可复用方法："
            + "；".join(str(item.get("display_name") or "") for item in skill_refs[:3])
        )
    return str(redact("。".join(part for part in parts if part)))[:1200]


def _experience_summary(*, user_text: str, assistant_text: str, turn_id: str) -> str:
    user_preview = str(redact(user_text)).strip()[:300]
    assistant_preview = str(redact(assistant_text)).strip()[:300]
    return (
        f"工作台反思经验：turn {turn_id} 中用户目标是“{user_preview or '空输入'}”，"
        f"系统回复策略是“{assistant_preview or '无助手回复'}”。"
        "后续相似会话应自动加载稳定记忆、可用方法和上下文文件摘要，"
        "但真实执行仍要经过工具、安全检查、确认、权限和资产授权。"
    )


def _context_file_key(member_id: str, conversation_id: str | None) -> str:
    return f"{member_id}:{conversation_id or 'member'}"


def _context_file_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_id": row["version_id"],
        "version_index": row["version_index"],
        "summary": row["summary_text"],
        "artifact_uri": row["artifact_uri"],
        "artifact_checksum": row["artifact_checksum"],
        "created_at": row["created_at"],
    }


def _checksum(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _safe_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "context"


def _ref_delta(
    old_refs: list[dict[str, Any]],
    new_refs: list[dict[str, Any]],
    *,
    key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    old_by_key = {str(item.get(key)): item for item in old_refs if item.get(key)}
    new_by_key = {str(item.get(key)): item for item in new_refs if item.get(key)}
    added = [new_by_key[item] for item in sorted(set(new_by_key) - set(old_by_key))]
    removed = [old_by_key[item] for item in sorted(set(old_by_key) - set(new_by_key))]
    return added, removed


def _dedupe_refs(refs: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in refs:
        value = str(item.get(key) or "")
        if value and value in seen:
            continue
        if value:
            seen.add(value)
        deduped.append(item)
    return deduped
