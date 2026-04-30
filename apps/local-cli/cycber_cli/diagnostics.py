from __future__ import annotations

from typing import Any

from cycber_cli.http_client import ApiError, CycberApiClient


async def turn_diagnostics(client: CycberApiClient, turn_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, getter in {
        "brain": client.brain_decision,
        "semantic": client.semantic_review,
        "tone_policy": client.tone_policy,
        "response_quality": client.response_quality,
    }.items():
        try:
            result[key] = await getter(turn_id)
        except ApiError as exc:
            result[key] = {"status": "not_available", "error": exc.payload}
    return result
