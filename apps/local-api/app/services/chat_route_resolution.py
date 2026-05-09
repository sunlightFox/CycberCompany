from __future__ import annotations

from core_types import ErrorCode

from app.schemas.chat_routes import ModelRouteResolution


class ChatRouteResolutionService:
    def resolve_model_route_failure(
        self,
        *,
        available_brains: list[dict[str, object]],
        privacy_level: str,
    ) -> ModelRouteResolution:
        brain_ids = [
            str(item.get("brain_id") or "")
            for item in available_brains
            if str(item.get("brain_id") or "").strip()
        ]
        has_local_brain = any(
            bool(brain.get("is_local")) for brain in available_brains
        )
        if privacy_level == "high" and not has_local_brain:
            return ModelRouteResolution(
                route_status="blocked_by_privacy",
                failure_code=ErrorCode.MODEL_ROUTE_BLOCKED_BY_PRIVACY.value,
                retryable=False,
                degrade_allowed=True,
                privacy_level=privacy_level,
                available_brain_ids=brain_ids,
                reason="high_privacy_requires_local_brain",
            )
        if not available_brains:
            return ModelRouteResolution(
                route_status="not_configured",
                failure_code=ErrorCode.MODEL_NOT_CONFIGURED.value,
                retryable=False,
                degrade_allowed=True,
                privacy_level=privacy_level,
                available_brain_ids=[],
                reason="no_routable_brains",
            )
        return ModelRouteResolution(
            route_status="route_not_found",
            failure_code=ErrorCode.MODEL_ROUTE_NOT_FOUND.value,
            retryable=False,
            degrade_allowed=True,
            privacy_level=privacy_level,
            available_brain_ids=brain_ids,
            reason="brains_available_but_no_route_selected",
        )
