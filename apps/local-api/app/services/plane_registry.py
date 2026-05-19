from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ControlPlaneRegistry:
    routes: dict[str, Any]
    startup: dict[str, Any]
    workers: dict[str, Any]


@dataclass
class RuntimePlaneRegistry:
    session_runtime: Any
    chat_runtime: Any
    agent_runtime: Any
    channel_ingress_runtime: Any


@dataclass
class CapabilityPlaneRegistry:
    browser_search_capability: Any
    browser_research_runtime: Any
    tool_runtime: Any
    skill_runtime: Any
    mcp_runtime: Any


@dataclass
class PolicyPlaneRegistry:
    persona_runtime: Any
    heart_runtime: Any
    tone_policy_runtime: Any
    response_quality_runtime: Any
