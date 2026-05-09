from __future__ import annotations

from typing import Any


class ModelCapabilityMatrix:
    def describe(self, brain: dict[str, Any]) -> dict[str, Any]:
        return {
            "brain_id": brain.get("brain_id"),
            "provider": brain.get("provider"),
            "model_name": brain.get("model_name"),
            "protocol_family": brain.get("protocol_family"),
            "request_format": brain.get("request_format"),
            "response_format": brain.get("response_format"),
            "supports_stream": bool(brain.get("supports_stream", True)),
            "supports_tools": bool(brain.get("supports_tools")),
            "supports_vision": bool(brain.get("supports_vision")),
        }
