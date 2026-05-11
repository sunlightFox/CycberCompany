from __future__ import annotations

from typing import Any

TERMINAL_LANE_MAIN = "main"
TERMINAL_LANE_READONLY = "readonly"
TERMINAL_LANE_BROWSER_ASSIST = "browser_assist"
TERMINAL_LANE_BACKGROUND = "background"
TERMINAL_LANE_RECOVERY = "recovery"

TERMINAL_LANES = (
    TERMINAL_LANE_MAIN,
    TERMINAL_LANE_READONLY,
    TERMINAL_LANE_BROWSER_ASSIST,
    TERMINAL_LANE_BACKGROUND,
    TERMINAL_LANE_RECOVERY,
)


def select_terminal_lane(
    *,
    tool_name: str,
    command: str,
    terminal_policy: dict[str, Any] | None,
) -> str:
    lowered = command.lower()
    if tool_name == "terminal.read_log":
        return TERMINAL_LANE_READONLY
    if tool_name == "terminal.stop":
        return TERMINAL_LANE_RECOVERY
    if any(marker in lowered for marker in ("recover", "resume", "rollback", "repair")):
        return TERMINAL_LANE_RECOVERY
    if any(marker in lowered for marker in ("playwright", "chrome", "edge", "firefox")):
        return TERMINAL_LANE_BROWSER_ASSIST
    if terminal_policy is None:
        return TERMINAL_LANE_MAIN
    reason = str(terminal_policy.get("reason") or "")
    reason_codes = [str(item) for item in terminal_policy.get("reason_codes") or []]
    if any("network_write" in item for item in reason_codes):
        return TERMINAL_LANE_BACKGROUND
    if reason == "mutation_requires_approval":
        return TERMINAL_LANE_MAIN
    return TERMINAL_LANE_READONLY


def classify_terminal_execution_semantics(
    *,
    tool_name: str,
    command: str,
    terminal_policy: dict[str, Any] | None,
    lane: str,
) -> dict[str, Any]:
    lowered = command.lower()
    reason = str((terminal_policy or {}).get("reason") or "")
    reason_codes = [str(item) for item in (terminal_policy or {}).get("reason_codes") or []]
    if tool_name == "terminal.read_log":
        command_class = "log_read"
    elif tool_name == "terminal.stop":
        command_class = "stop"
    elif any("network_write" in item for item in reason_codes):
        command_class = "network_write"
    elif reason == "mutation_requires_approval":
        command_class = "mutation"
    elif any(marker in lowered for marker in ("curl ", "wget ", "invoke-webrequest", "irm ")):
        command_class = "network_read"
    elif lane == TERMINAL_LANE_RECOVERY:
        command_class = "recovery"
    else:
        command_class = "readonly"
    queue_mode = "bounded_parallel" if lane == TERMINAL_LANE_BACKGROUND else "serialized"
    return {
        "lane": lane,
        "command_class": command_class,
        "queue_mode": queue_mode,
        "sync_execution": True,
    }
