from __future__ import annotations

from typing import Any

from core_types import ApiModel, EntityId, WorkbenchContext
from pydantic import Field


class AgentWorkbenchJobItem(ApiModel):
    job_id: EntityId
    organization_id: EntityId
    turn_id: EntityId | None = None
    idempotency_key: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int = 3
    next_run_at: str | None = None
    locked_by: str | None = None
    locked_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class AgentWorkbenchJobListResponse(ApiModel):
    items: list[AgentWorkbenchJobItem] = Field(default_factory=list)


class AgentWorkbenchReflectRequest(ApiModel):
    mode: str = "enqueue"


class AgentWorkbenchReflectResponse(ApiModel):
    status: str
    job: AgentWorkbenchJobItem | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class AgentWorkbenchProcessResponse(ApiModel):
    processed_jobs: int


class AgentWorkbenchContextPack(ApiModel):
    context_pack_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    summary_text: str
    memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    context_file_refs: list[dict[str, Any]] = Field(default_factory=list)
    working_state: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    token_estimate: int = 0
    status: str = "active"
    trace_id: EntityId | None = None
    created_at: str

    def as_workbench_context(self) -> WorkbenchContext:
        version_id = None
        if self.context_file_refs:
            version_id = self.context_file_refs[0].get("version_id")
        return WorkbenchContext(
            context_pack_id=self.context_pack_id,
            context_file_version_id=version_id,
            summary=self.summary_text,
            memory_refs=self.memory_refs,
            skill_refs=self.skill_refs,
            context_file_refs=self.context_file_refs,
            source_refs=self.source_refs,
            token_estimate=self.token_estimate,
            generated_at=self.created_at,
        )


class AgentWorkbenchContextPackBuildRequest(ApiModel):
    member_id: EntityId
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    persist: bool = False


class AgentWorkbenchContextPackResponse(ApiModel):
    pack: AgentWorkbenchContextPack | None = None


class AgentContextFileVersion(ApiModel):
    version_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId
    conversation_id: EntityId | None = None
    context_file_key: str
    version_index: int
    status: str
    summary_text: str
    artifact_uri: str
    artifact_checksum: str
    artifact_size_bytes: int = 0
    source_turn_id: EntityId | None = None
    source_trace_id: EntityId | None = None
    context_pack_id: EntityId | None = None
    diff_base_version_id: EntityId | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class AgentContextFileVersionListResponse(ApiModel):
    items: list[AgentContextFileVersion] = Field(default_factory=list)


class AgentContextFileVersionResponse(ApiModel):
    version: AgentContextFileVersion


class AgentContextFileReplay(ApiModel):
    version: AgentContextFileVersion
    artifact_exists: bool
    checksum_matches: bool
    artifact_preview: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)


class AgentContextFileReplayResponse(ApiModel):
    replay: AgentContextFileReplay


class AgentContextFileDiff(ApiModel):
    from_version_id: EntityId
    to_version_id: EntityId
    summary_changed: bool
    artifact_checksum_changed: bool
    added_memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    removed_memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    added_skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    removed_skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_ref_delta: dict[str, Any] = Field(default_factory=dict)


class AgentContextFileDiffResponse(ApiModel):
    diff: AgentContextFileDiff
