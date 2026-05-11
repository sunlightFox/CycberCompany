from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class HeuristicRule:
    name: str
    owner: str
    current_layer: str
    classification: str
    replace_target: str
    allowed_to_terminate_mainline: bool


_PHASE89_HEURISTICS: tuple[HeuristicRule, ...] = (
    HeuristicRule(
        name="deterministic_boundary_reply",
        owner="chat.py",
        current_layer="chat_turn_execution.direct_response_chain",
        classification="hard_guard",
        replace_target="deterministic_boundary_guard",
        allowed_to_terminate_mainline=True,
    ),
    HeuristicRule(
        name="chat_quality_policy",
        owner="chat_quality.py",
        current_layer="chat_turn_execution.direct_response_chain",
        classification="hard_guard",
        replace_target="quality_boundary_policy",
        allowed_to_terminate_mainline=True,
    ),
    HeuristicRule(
        name="pending_action_resolution",
        owner="natural_chat.py",
        current_layer="natural_chat_gateway",
        classification="hard_guard",
        replace_target="pending_state_resolution",
        allowed_to_terminate_mainline=True,
    ),
    HeuristicRule(
        name="deterministic_execution_state_reply",
        owner="chat.py",
        current_layer="chat_turn_execution.direct_response_chain",
        classification="soft_heuristic",
        replace_target="brain_decision_and_response_composer",
        allowed_to_terminate_mainline=False,
    ),
    HeuristicRule(
        name="deterministic_latest_instruction_reply",
        owner="chat.py",
        current_layer="chat_turn_execution.direct_response_chain",
        classification="soft_heuristic",
        replace_target="context_gateway_latest_instruction_override",
        allowed_to_terminate_mainline=False,
    ),
    HeuristicRule(
        name="pending_clarification_followup",
        owner="chat.py",
        current_layer="chat_turn_execution.direct_response_chain",
        classification="soft_heuristic",
        replace_target="pending_state_and_brain_decision",
        allowed_to_terminate_mainline=False,
    ),
    HeuristicRule(
        name="plain_analysis_request",
        owner="chat.py",
        current_layer="prompt_options_and_brain_input",
        classification="soft_heuristic",
        replace_target="brain_decision",
        allowed_to_terminate_mainline=False,
    ),
    HeuristicRule(
        name="explicit_continuation",
        owner="chat.py",
        current_layer="prompt_options_and_context_gateway",
        classification="soft_heuristic",
        replace_target="context_gateway_continuation",
        allowed_to_terminate_mainline=False,
    ),
    HeuristicRule(
        name="short_followup",
        owner="chat.py",
        current_layer="prompt_options_and_context_gateway",
        classification="soft_heuristic",
        replace_target="context_gateway_followup_detection",
        allowed_to_terminate_mainline=False,
    ),
    HeuristicRule(
        name="natural_plain_reply",
        owner="natural_chat.py",
        current_layer="natural_chat_gateway",
        classification="soft_heuristic",
        replace_target="response_composer_after_pending_resolution",
        allowed_to_terminate_mainline=False,
    ),
)


def phase89_heuristic_inventory() -> list[dict[str, Any]]:
    return [asdict(item) for item in _PHASE89_HEURISTICS]


def phase89_heuristic_summary() -> dict[str, Any]:
    inventory = phase89_heuristic_inventory()
    hard_guard_count = sum(1 for item in inventory if item["classification"] == "hard_guard")
    soft_heuristic_count = sum(
        1 for item in inventory if item["classification"] == "soft_heuristic"
    )
    return {
        "phase89_registry_version": "phase89.false_interception_registry.v1",
        "hard_guard_count": hard_guard_count,
        "deprecated_soft_heuristic_count": soft_heuristic_count,
        "terminal_soft_heuristic_count": sum(
            1
            for item in inventory
            if item["classification"] == "soft_heuristic"
            and item["allowed_to_terminate_mainline"]
        ),
        "inventory": inventory,
    }
