from __future__ import annotations

from typing import Any

from core_types import ErrorCode
from safety_service import PrivacyClassification, SafetyService

from app.schemas.chat_routes import ModelRouteResolution
from app.services.chat_model import ChatModelCoordinator
from app.services.chat_safety import planner_privacy_context


class ChatPrivacyCoordinator:
    """Keeps chat privacy classification and local-first routing policy together."""

    def __init__(
        self,
        *,
        safety: SafetyService | None = None,
        model_coordinator: ChatModelCoordinator | None = None,
    ) -> None:
        self._safety = safety or SafetyService()
        self._model = model_coordinator or ChatModelCoordinator()

    def classify(self, user_text: str) -> PrivacyClassification:
        return self._safety.classify_chat_input(user_text)

    def planner_context(
        self,
        *,
        privacy_level: str,
        allow_cloud: bool,
        sensitivity_hits: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        return planner_privacy_context(
            privacy_level=privacy_level,
            allow_cloud=allow_cloud,
            sensitivity_hits=sensitivity_hits,
        )

    def model_route_error(
        self,
        available_brains: list[dict[str, Any]],
        privacy_level: str,
    ) -> ErrorCode:
        return self._model.route_error_code(available_brains, privacy_level)

    def model_route_resolution(
        self,
        available_brains: list[dict[str, Any]],
        privacy_level: str,
    ) -> ModelRouteResolution:
        return self._model.route_resolution(available_brains, privacy_level)
