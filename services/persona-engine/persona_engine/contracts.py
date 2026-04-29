from __future__ import annotations

from typing import Any

from core_types import ApiModel, EntityId
from pydantic import Field


class PersonaProfile(ApiModel):
    persona_profile_id: EntityId
    summary: str
    tone_policy: dict[str, Any] = Field(default_factory=dict)
    disclosure_policy: dict[str, Any] = Field(default_factory=dict)
    risk_tone_policy: dict[str, Any] = Field(default_factory=dict)
    allowed_modes: list[str] = Field(default_factory=lambda: ["default"])
    default_mode: str = "default"


class PersonaEngine:
    async def get_profile(self, persona_profile_id: EntityId) -> PersonaProfile:
        return PersonaProfile(
            persona_profile_id=persona_profile_id,
            summary="Calm, direct, warm, conclusion-first.",
            tone_policy={
                "conciseness": 0.72,
                "directness": 0.78,
                "warmth": 0.68,
                "technical_depth": 0.66,
            },
            disclosure_policy={
                "capability_boundary_disclosure": True,
                "uncertainty_disclosure": True,
                "tool_usage_notice": "when_tool_or_task_is_required",
            },
            risk_tone_policy={
                "approval_scene_tone": "clear_and_calm",
                "security_block_scene_tone": "firm_and_explanatory",
                "high_impact_scene_tone": "low_anthropomorphic",
            },
            allowed_modes=["default", "concise", "deep_dialogue", "safety_boundary"],
            default_mode="default",
        )
