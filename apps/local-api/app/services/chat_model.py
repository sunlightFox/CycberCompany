from __future__ import annotations

from typing import Any

from brain.adapters import estimate_messages_tokens
from core_types import ContextPacket, ErrorCode
from response_composer.chat_voice import (
    ChatPromptAssembler,
    DynamicContextMode,
    PromptAssemblyResult,
    PromptMode,
)


class ChatModelCoordinator:
    """Builds model-safe chat inputs and owns model-route failure semantics."""

    def __init__(self, *, prompt_assembler: ChatPromptAssembler | None = None) -> None:
        self._prompt_assembler = prompt_assembler or ChatPromptAssembler()

    def model_messages(
        self,
        context: ContextPacket,
        user_text: str,
        *,
        prompt_mode: PromptMode = "full",
        channel_profile: str | None = None,
        delivery_mode: str | None = None,
        sender_label: str | None = None,
        turn_id: str | None = None,
        include_dynamic_context: bool = False,
        include_trusted_context: bool = True,
        include_untrusted_context: bool = True,
        include_history: bool = True,
        include_session_summary: bool = False,
        recent_history_limit: int = 6,
        dynamic_context_mode: DynamicContextMode = "index",
        prompt_profile: str | None = None,
    ) -> list[dict[str, str]]:
        return self._prompt_assembler.model_messages(
            context,
            user_text,
            prompt_mode=prompt_mode,
            channel_profile=channel_profile,
            delivery_mode=delivery_mode,
            sender_label=sender_label,
            turn_id=turn_id,
            include_dynamic_context=include_dynamic_context,
            include_trusted_context=include_trusted_context,
            include_untrusted_context=include_untrusted_context,
            include_history=include_history,
            include_session_summary=include_session_summary,
            recent_history_limit=recent_history_limit,
            dynamic_context_mode=dynamic_context_mode,
            prompt_profile=prompt_profile,
        )

    def model_assembly(
        self,
        context: ContextPacket,
        user_text: str,
        *,
        prompt_mode: PromptMode = "full",
        channel_profile: str | None = None,
        delivery_mode: str | None = None,
        sender_label: str | None = None,
        turn_id: str | None = None,
        include_dynamic_context: bool = False,
        include_trusted_context: bool = True,
        include_untrusted_context: bool = True,
        include_history: bool = True,
        include_session_summary: bool = False,
        recent_history_limit: int = 6,
        dynamic_context_mode: DynamicContextMode = "index",
        prompt_profile: str | None = None,
    ) -> PromptAssemblyResult:
        return self._prompt_assembler.assemble(
            context,
            user_text,
            prompt_mode=prompt_mode,
            channel_profile=channel_profile,
            delivery_mode=delivery_mode,
            sender_label=sender_label,
            turn_id=turn_id,
            include_dynamic_context=include_dynamic_context,
            include_trusted_context=include_trusted_context,
            include_untrusted_context=include_untrusted_context,
            include_history=include_history,
            include_session_summary=include_session_summary,
            recent_history_limit=recent_history_limit,
            dynamic_context_mode=dynamic_context_mode,
            prompt_profile=prompt_profile,
        )

    def prompt_metadata(
        self,
        context: ContextPacket,
        user_text: str,
        *,
        prompt_mode: PromptMode = "full",
        channel_profile: str | None = None,
        delivery_mode: str | None = None,
        sender_label: str | None = None,
        turn_id: str | None = None,
        include_dynamic_context: bool = False,
        include_trusted_context: bool = True,
        include_untrusted_context: bool = True,
        include_history: bool = True,
        include_session_summary: bool = False,
        recent_history_limit: int = 6,
        dynamic_context_mode: DynamicContextMode = "index",
        prompt_profile: str | None = None,
    ) -> dict[str, Any]:
        return self.model_assembly(
            context,
            user_text,
            prompt_mode=prompt_mode,
            channel_profile=channel_profile,
            delivery_mode=delivery_mode,
            sender_label=sender_label,
            turn_id=turn_id,
            include_dynamic_context=include_dynamic_context,
            include_trusted_context=include_trusted_context,
            include_untrusted_context=include_untrusted_context,
            include_history=include_history,
            include_session_summary=include_session_summary,
            recent_history_limit=recent_history_limit,
            dynamic_context_mode=dynamic_context_mode,
            prompt_profile=prompt_profile,
        ).metadata

    def estimate_input_tokens(self, context: ContextPacket, user_text: str) -> int:
        return estimate_messages_tokens(self.model_messages(context, user_text))

    def route_error_code(
        self,
        available_brains: list[dict[str, Any]],
        privacy_level: str,
    ) -> ErrorCode:
        if privacy_level == "high" and not any(
            bool(brain.get("is_local")) for brain in available_brains
        ):
            return ErrorCode.MODEL_ROUTE_BLOCKED_BY_PRIVACY
        if not available_brains:
            return ErrorCode.MODEL_NOT_CONFIGURED
        return ErrorCode.MODEL_ROUTE_NOT_FOUND
