from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from core_types import FailureExperienceRecord, RegressionCandidateRecord, RiskLevel
from trace_service import redact

from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.memory_repo import MemoryRepository
from app.services.audit import AuditEventService
from app.services.memory import MemoryService


class FailureExperienceService:
    def __init__(
        self,
        *,
        repo: MemoryRepository,
        member_repo: MemberRepository,
        audit_service: AuditEventService,
        memory_service: MemoryService,
    ) -> None:
        self._repo = repo
        self._members = member_repo
        self._audit = audit_service
        self._memory = memory_service

    async def record_failure(
        self,
        *,
        member_id: str,
        failure_class: str,
        summary_text: str,
        reason_code: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        impact_scope: str | None = None,
        severity: str = "medium",
        evidence_refs: list[dict[str, Any]] | None = None,
        evidence_summary: str | None = None,
        source_payload: dict[str, Any] | None = None,
    ) -> FailureExperienceRecord:
        member = await self._members.get_member(member_id)
        if member is None:
            raise ValueError(f"member not found: {member_id}")
        now = utc_now_iso()
        evidence_refs = list(evidence_refs or [])
        source_payload = redact(source_payload or {})
        evidence_summary = evidence_summary.strip() if evidence_summary else None
        recurrence_key = _recurrence_key(
            member_id=member_id,
            failure_class=failure_class,
            reason_code=reason_code,
            impact_scope=impact_scope,
        )
        existing = await self._repo.list_failure_experience_records(
            member_id=member_id,
            recurrence_key=recurrence_key,
            created_after=(utc_now() - timedelta(days=7)).isoformat(),
            limit=100,
        )
        active_existing = [
            item for item in existing if str(item.get("review_status") or "") != "tombstoned"
        ]
        recurrence_count = len(active_existing) + 1
        low_evidence = not reason_code or (not evidence_refs and not evidence_summary)
        high_risk = (
            failure_class == "false_completion"
            or (reason_code or "").lower() == "false_completion"
            or _is_high_risk(reason_code, impact_scope)
        )
        human_review_required = low_evidence or high_risk
        memory_decision = "not_written"
        review_status = "not_required"
        advisory_status = "inactive"
        memory_id: str | None = None
        if human_review_required:
            memory_decision = "needs_review"
            review_status = "pending_review"
        else:
            try:
                memory = await self._memory.write_failure_advisory_memory(
                    member_id=member_id,
                    summary_text=summary_text,
                    source={
                        "type": "failure_experience",
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                        "task_id": task_id,
                        "trace_id": trace_id,
                        "captured_at": now,
                    },
                    payload={
                        "failure_class": failure_class,
                        "reason_code": reason_code,
                        "impact_scope": impact_scope,
                        "severity": severity,
                        "evidence_summary": evidence_summary,
                        "evidence_refs": evidence_refs,
                    },
                    trace_id=trace_id,
                )
                memory_id = memory.memory_id
                memory_decision = "written"
                advisory_status = "advisory_only"
            except Exception:
                memory_decision = "write_failed"
                advisory_status = "inactive"
        data = {
            "failure_id": new_id("fail"),
            "organization_id": member["organization_id"],
            "member_id": member_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "task_id": task_id,
            "trace_id": trace_id,
            "memory_id": memory_id,
            "failure_class": failure_class,
            "reason_code": reason_code,
            "impact_scope": impact_scope,
            "severity": severity,
            "summary_text": summary_text,
            "evidence_refs": evidence_refs,
            "evidence_summary": evidence_summary,
            "source_payload": source_payload,
            "recurrence_key": recurrence_key,
            "recurrence_count": recurrence_count,
            "memory_decision": memory_decision,
            "review_status": review_status,
            "advisory_status": advisory_status,
            "human_review_required": human_review_required,
            "tombstone_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_failure_experience_record(data)
        if recurrence_count >= 2:
            await self._upsert_regression_candidate(data)
        return FailureExperienceRecord(**data)

    async def list_failure_experiences(
        self,
        *,
        member_id: str | None = None,
        failure_class: str | None = None,
        review_status: str | None = None,
        limit: int = 50,
    ) -> list[FailureExperienceRecord]:
        rows = await self._repo.list_failure_experience_records(
            member_id=member_id,
            failure_class=failure_class,
            review_status=review_status,
            limit=limit,
        )
        return [FailureExperienceRecord(**row) for row in rows]

    async def review_failure(
        self,
        failure_id: str,
        *,
        action: str,
        tombstone_reason: str | None = None,
    ) -> FailureExperienceRecord:
        row = await self._repo.get_failure_experience_record(failure_id)
        if row is None:
            raise ValueError(f"failure not found: {failure_id}")
        updates: dict[str, Any] = {"updated_at": utc_now_iso()}
        if action == "approve":
            updates["review_status"] = "approved"
            updates["advisory_status"] = "advisory_only"
            if row.get("memory_decision") != "written":
                memory = await self._memory.write_failure_advisory_memory(
                    member_id=str(row.get("member_id")),
                    summary_text=str(row.get("summary_text")),
                    source={
                        "type": "failure_experience_review",
                        "conversation_id": row.get("conversation_id"),
                        "turn_id": row.get("turn_id"),
                        "task_id": row.get("task_id"),
                        "trace_id": row.get("trace_id"),
                        "captured_at": updates["updated_at"],
                    },
                    payload={
                        "failure_class": row.get("failure_class"),
                        "reason_code": row.get("reason_code"),
                        "impact_scope": row.get("impact_scope"),
                        "severity": row.get("severity"),
                        "evidence_summary": row.get("evidence_summary"),
                        "evidence_refs": row.get("evidence_refs") or [],
                    },
                    trace_id=row.get("trace_id"),
                )
                updates["memory_decision"] = "written"
                updates["memory_id"] = memory.memory_id
            elif row.get("memory_decision") == "needs_review":
                updates["memory_decision"] = "written"
        elif action == "reject":
            updates["review_status"] = "rejected"
            updates["advisory_status"] = "inactive"
            updates["memory_decision"] = "rejected"
        elif action == "tombstone":
            updates["review_status"] = "tombstoned"
            updates["advisory_status"] = "suppressed"
            updates["tombstone_reason"] = tombstone_reason or "manual_tombstone"
        elif action == "suppress_advisory":
            updates["advisory_status"] = "suppressed"
        else:
            raise ValueError(f"unsupported action: {action}")
        await self._repo.update_failure_experience_record(failure_id, updates)
        updated = await self._repo.get_failure_experience_record(failure_id)
        assert updated is not None
        await self._audit.write_event(
            actor_type="system",
            action=f"failure_experience.{action}",
            object_type="failure_experience",
            object_id=failure_id,
            summary=f"failure experience {action}",
            risk_level=RiskLevel.R2,
            payload=redact(updated),
            trace_id=updated.get("trace_id"),
        )
        regression = await self._repo.get_regression_candidate_by_recurrence_key(
            str(updated.get("recurrence_key"))
        )
        if regression is not None:
            await self._audit.write_event(
                actor_type="system",
                action=f"regression_candidate.observe.{action}",
                object_type="regression_candidate",
                object_id=str(regression.get("candidate_id")),
                summary="regression candidate linked to reviewed failure experience",
                risk_level=RiskLevel.R1,
                payload={"failure_id": failure_id, "candidate_id": regression.get("candidate_id")},
                trace_id=updated.get("trace_id"),
            )
        return FailureExperienceRecord(**updated)

    async def list_regression_candidates(
        self,
        *,
        status: str | None = None,
        failure_class: str | None = None,
        limit: int = 50,
    ) -> list[RegressionCandidateRecord]:
        rows = await self._repo.list_regression_candidates(
            status=status,
            failure_class=failure_class,
            limit=limit,
        )
        return [RegressionCandidateRecord(**row) for row in rows]

    async def recall_advisories(
        self,
        *,
        member_id: str,
        query: str,
        limit: int = 3,
    ) -> list[FailureExperienceRecord]:
        rows = await self._repo.list_failure_experience_records(
            member_id=member_id,
            review_status="approved,not_required",
            advisory_status="advisory_only",
            query=query,
            limit=limit,
        )
        filtered = [
            row
            for row in rows
            if row.get("memory_decision") == "written"
            and row.get("review_status") != "tombstoned"
        ]
        return [FailureExperienceRecord(**row) for row in filtered]

    def runtime_diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "failure_experience_service",
            "contract_version": "phase94.failure_experience_governance.v1",
            "review_actions": ["approve", "reject", "tombstone", "suppress_advisory"],
            "regression_threshold": {"window_days": 7, "min_recurrence_count": 2},
        }

    async def _upsert_regression_candidate(self, failure_row: dict[str, Any]) -> None:
        existing = await self._repo.get_regression_candidate_by_recurrence_key(
            str(failure_row["recurrence_key"])
        )
        now = utc_now_iso()
        if existing is None:
            await self._repo.insert_regression_candidate(
                {
                    "candidate_id": new_id("regcand"),
                    "failure_id": failure_row["failure_id"],
                    "source_turn_id": failure_row.get("turn_id"),
                    "source_trace_id": failure_row.get("trace_id"),
                    "candidate_type": "chat_regression",
                    "status": "open",
                    "recurrence_key": failure_row["recurrence_key"],
                    "recurrence_count": failure_row["recurrence_count"],
                    "failure_class": failure_row["failure_class"],
                    "reason_code": failure_row.get("reason_code"),
                    "summary_text": failure_row["summary_text"],
                    "evidence_refs": failure_row.get("evidence_refs", []),
                    "release_gate_id": None,
                    "accepted_into_suite": None,
                    "accepted_case_key": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            return
        await self._repo.update_regression_candidate(
            str(existing["candidate_id"]),
            {
                "failure_id": failure_row["failure_id"],
                "source_turn_id": failure_row.get("turn_id"),
                "source_trace_id": failure_row.get("trace_id"),
                "recurrence_count": failure_row["recurrence_count"],
                "summary_text": failure_row["summary_text"],
                "evidence_refs": failure_row.get("evidence_refs", []),
                "updated_at": now,
            },
        )


def _recurrence_key(
    *,
    member_id: str,
    failure_class: str,
    reason_code: str | None,
    impact_scope: str | None,
) -> str:
    raw = "|".join(
        [
            member_id,
            failure_class,
            reason_code or "unknown",
            impact_scope or "general",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _is_high_risk(reason_code: str | None, impact_scope: str | None) -> bool:
    text = " ".join([reason_code or "", impact_scope or ""]).lower()
    return any(token in text for token in ("privacy", "safety", "cross_session", "secret"))
