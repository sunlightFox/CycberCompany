from __future__ import annotations

from typing import Any

from pydantic import Field

from core_types.common import ApiModel


class SessionContext(ApiModel):
    stable_identity_block: str = ""
    stable_user_profile_block: str = ""
    current_conversation_summary: str = ""
    current_open_loops: list[str] = Field(default_factory=list)
    current_commitments: list[str] = Field(default_factory=list)
    relevant_recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    relevant_memory_items: list[dict[str, Any]] = Field(default_factory=list)
    current_action_facts: dict[str, Any] = Field(default_factory=dict)
    compaction_recovery_summary: str = ""


class SilentContinuityRecord(ApiModel):
    continuity_summary: str
    user_state_hint: str | None = None
    assistant_commitments: list[str] = Field(default_factory=list)
    followup_candidates: list[str] = Field(default_factory=list)
    topic_anchor: str | None = None
    expiry_policy: dict[str, Any] = Field(default_factory=dict)
    source_turn_id: str
    trace_id: str | None = None
    profile_updates: dict[str, Any] = Field(default_factory=dict)
