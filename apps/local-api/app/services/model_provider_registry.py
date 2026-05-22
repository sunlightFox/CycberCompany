from __future__ import annotations

from typing import Any

from app.services.model_capability_matrix import ModelCapabilityMatrix


class ModelProviderRegistry:
    def __init__(self, *, secret_store: Any, client_cls: type[Any]) -> None:
        self._secrets = secret_store
        self._client_cls = client_cls
        self._capability_matrix = ModelCapabilityMatrix()

    def build_client(self, brain: dict[str, Any]) -> Any:
        endpoint = str(brain["endpoint"])
        api_key = self._secrets.get_secret(brain.get("api_key_ref"))
        privacy_policy = brain.get("privacy_policy")
        privacy = privacy_policy if isinstance(privacy_policy, dict) else {}
        kwargs = {
            "protocol_family": str(brain.get("protocol_family") or ""),
            "request_format": str(brain.get("request_format") or ""),
            "response_format": str(brain.get("response_format") or ""),
            "supports_stream": brain.get("supports_stream"),
            "reasoning_effort": privacy.get("reasoning_effort"),
            "text_verbosity": privacy.get("text_verbosity"),
        }
        try:
            return self._client_cls(endpoint, api_key, **kwargs)
        except TypeError:
            return self._client_cls(endpoint, api_key)

    def capability_summary(self, brain: dict[str, Any]) -> dict[str, Any]:
        return self._capability_matrix.describe(brain)
