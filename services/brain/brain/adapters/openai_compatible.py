from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from core_types import ErrorCode

from brain.adapters.errors import ModelAdapterError, map_http_status
from brain.adapters.types import CancelToken, ModelChatRequest, ModelChatResult, ModelStreamEvent


class OpenAICompatibleClient:
    def __init__(
        self,
        endpoint: str,
        api_key: str | None = None,
        *,
        protocol_family: str = "auto",
        request_format: str = "chat_completions",
        response_format: str = "auto",
        supports_stream: bool | None = None,
        reasoning_effort: str | None = None,
        text_verbosity: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._protocol_family = protocol_family or "auto"
        self._request_format = request_format or "chat_completions"
        self._response_format = response_format or "auto"
        self._supports_stream = True if supports_stream is None else bool(supports_stream)
        self._reasoning_effort = _safe_choice(
            reasoning_effort,
            {"minimal", "low", "medium", "high"},
        )
        self._text_verbosity = _safe_choice(text_verbosity, {"low", "medium", "high"})
        self._transport = transport

    async def stream_chat(
        self,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> AsyncIterator[ModelStreamEvent]:
        if cancel_token.cancelled:
            yield ModelStreamEvent(event="cancelled", error_code=ErrorCode.TURN_CANCELLED.value)
            return
        if not self._supports_stream:
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型未启用流式能力")

        yield ModelStreamEvent(
            event="started",
            metadata={"protocol_family": self._selected_protocol_family(), "mode": "stream"},
        )
        payload = self._payload(request, stream=True)
        timeout = self._timeout(request)
        emitted_payload = False
        for attempt in range(request.retry_count + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    transport=self._transport,
                ) as client:
                    async with client.stream(
                        "POST",
                        self._request_url(),
                        headers=self._headers(),
                        json=payload,
                    ) as response:
                        if response.status_code >= 400:
                            body = (await response.aread()).decode("utf-8", errors="replace")
                            raise map_http_status(response.status_code, body[:500])
                        completed = False
                        lines = response.aiter_lines()
                        while True:
                            try:
                                line = await _next_stream_line(
                                    lines,
                                    first_token_timeout_seconds=(
                                        request.first_token_timeout_seconds
                                        if not emitted_payload
                                        else None
                                    ),
                                )
                            except StopAsyncIteration:
                                break
                            if cancel_token.cancelled:
                                yield ModelStreamEvent(
                                    event="cancelled",
                                    error_code=ErrorCode.TURN_CANCELLED.value,
                                )
                                return
                            event = self._parse_stream_line(line)
                            if event is None:
                                continue
                            event.metadata.setdefault(
                                "protocol_family",
                                self._selected_protocol_family(),
                            )
                            if event.event in {"delta", "usage_delta", "completed"}:
                                emitted_payload = True
                            if event.event == "completed":
                                completed = True
                            yield event
                            if completed:
                                return
                        if not completed:
                            raise ModelAdapterError(
                                ErrorCode.MODEL_STREAM_INTERRUPTED,
                                "模型流式响应提前结束",
                            )
            except httpx.TimeoutException as exc:
                if emitted_payload or attempt >= request.retry_count:
                    raise ModelAdapterError(ErrorCode.MODEL_TIMEOUT, "模型响应超时") from exc
            except httpx.ConnectError as exc:
                if emitted_payload or attempt >= request.retry_count:
                    raise ModelAdapterError(
                        ErrorCode.MODEL_UNAVAILABLE,
                        "无法连接模型服务",
                    ) from exc
            except httpx.RemoteProtocolError as exc:
                if emitted_payload or attempt >= request.retry_count:
                    raise ModelAdapterError(
                        ErrorCode.MODEL_PROTOCOL_ERROR,
                        "模型流式连接提前断开",
                    ) from exc
            except httpx.HTTPError as exc:
                if emitted_payload or attempt >= request.retry_count:
                    raise ModelAdapterError(
                        ErrorCode.MODEL_STREAM_INTERRUPTED,
                        "模型流式响应中断",
                    ) from exc
            except ModelAdapterError as exc:
                if exc.code not in {
                    ErrorCode.MODEL_UNAVAILABLE,
                    ErrorCode.MODEL_STREAM_INTERRUPTED,
                } or emitted_payload or attempt >= request.retry_count:
                    raise

    async def complete_chat(
        self,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> ModelChatResult:
        if cancel_token.cancelled:
            raise ModelAdapterError(ErrorCode.TURN_CANCELLED, "生成已取消")

        payload = self._payload(request, stream=False)
        timeout = self._timeout(request)
        response = None
        for attempt in range(request.retry_count + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                    response = await client.post(
                        self._request_url(),
                        headers=self._headers(),
                        json=payload,
                    )
                if response.status_code >= 400:
                    error = map_http_status(response.status_code, response.text[:500])
                    if error.code == ErrorCode.MODEL_UNAVAILABLE and attempt < request.retry_count:
                        continue
                    raise error
                break
            except httpx.TimeoutException as exc:
                if attempt >= request.retry_count:
                    raise ModelAdapterError(ErrorCode.MODEL_TIMEOUT, "模型响应超时") from exc
            except httpx.ConnectError as exc:
                if attempt >= request.retry_count:
                    raise ModelAdapterError(
                        ErrorCode.MODEL_UNAVAILABLE,
                        "无法连接模型服务",
                    ) from exc
            except httpx.RemoteProtocolError as exc:
                if attempt >= request.retry_count:
                    raise ModelAdapterError(
                        ErrorCode.MODEL_PROTOCOL_ERROR,
                        "模型非流式连接提前断开",
                    ) from exc
            except httpx.HTTPError as exc:
                if attempt >= request.retry_count:
                    raise ModelAdapterError(
                        ErrorCode.MODEL_PROTOCOL_ERROR,
                        "模型非流式请求失败",
                    ) from exc
        if response is None:
            raise ModelAdapterError(ErrorCode.MODEL_UNAVAILABLE, "无法连接模型服务")

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ModelAdapterError(
                ErrorCode.MODEL_PROTOCOL_ERROR,
                "模型响应不是合法 JSON",
            ) from exc
        try:
            text, finish_reason, usage, metadata = self._extract_completion(data)
        except ModelAdapterError as exc:
            if (
                exc.code == ErrorCode.MODEL_PROTOCOL_ERROR
                and self._supports_stream
                and not cancel_token.cancelled
            ):
                return await self._complete_via_stream(request, cancel_token)
            raise
        return ModelChatResult(
            text=text,
            usage=usage,
            finish_reason=finish_reason,
            metadata={
                **metadata,
                "protocol_family": self._selected_protocol_family(),
                "response_format": self._response_format,
            },
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _selected_protocol_family(self) -> str:
        family = (self._protocol_family or "auto").strip().lower()
        if family in {"responses", "chat_completions"}:
            return family
        request_format = (self._request_format or "").strip().lower()
        if request_format in {"responses", "chat_completions"}:
            return request_format
        response_format = (self._response_format or "").strip().lower()
        if response_format == "responses":
            return "responses"
        return "chat_completions"

    def _request_url(self) -> str:
        family = self._selected_protocol_family()
        if family == "responses":
            if self._endpoint.endswith("/responses"):
                return self._endpoint
            if self._endpoint.endswith("/v1"):
                return f"{self._endpoint}/responses"
            return f"{self._endpoint}/v1/responses"
        if self._endpoint.endswith("/chat/completions"):
            return self._endpoint
        if self._endpoint.endswith("/v1"):
            return f"{self._endpoint}/chat/completions"
        return f"{self._endpoint}/v1/chat/completions"

    def _payload(self, request: ModelChatRequest, *, stream: bool) -> dict[str, Any]:
        family = self._selected_protocol_family()
        if family == "responses":
            payload = {
                "model": request.model,
                "input": self._responses_input(request.messages),
                "temperature": request.temperature,
                "top_p": request.top_p,
                "max_output_tokens": request.max_output_tokens,
                "stream": stream,
            }
            reasoning_effort = _safe_choice(
                request.metadata.get("reasoning_effort") or self._reasoning_effort,
                {"minimal", "low", "medium", "high"},
            )
            text_verbosity = _safe_choice(
                request.metadata.get("text_verbosity") or self._text_verbosity,
                {"low", "medium", "high"},
            )
            if reasoning_effort:
                payload["reasoning"] = {"effort": reasoning_effort}
            if text_verbosity:
                payload["text"] = {"verbosity": text_verbosity}
            return payload
        return {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "max_tokens": request.max_output_tokens,
            "stream": stream,
        }

    def _responses_input(self, messages: list[dict[str, str]]) -> str | list[dict[str, Any]]:
        if len(messages) == 1 and messages[0].get("role") == "user":
            return str(messages[0].get("content") or "")
        items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role") or "user"
            content_type = "output_text" if role == "assistant" else "input_text"
            items.append(
                {
                    "role": role,
                    "content": [
                        {
                            "type": content_type,
                            "text": str(message.get("content") or ""),
                        }
                    ],
                }
            )
        return items

    def _extract_completion(
        self,
        data: Any,
    ) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        family = self._selected_protocol_family()
        if family == "responses":
            return _extract_responses_completion(data)
        return _extract_chat_completion(data)

    def _parse_stream_line(self, line: str) -> ModelStreamEvent | None:
        family = self._selected_protocol_family()
        if family == "responses":
            return _parse_responses_stream_line(line)
        return _parse_chat_stream_line(line)

    def _timeout(self, request: ModelChatRequest) -> httpx.Timeout:
        return httpx.Timeout(
            timeout=request.timeout_seconds,
            connect=min(10.0, float(request.timeout_seconds)),
            read=float(request.timeout_seconds),
        )

    async def _complete_via_stream(
        self,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> ModelChatResult:
        parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        async for event in self.stream_chat(request, cancel_token):
            if event.event == "delta" and event.text:
                parts.append(event.text)
            if event.usage:
                usage = event.usage
            if event.event == "completed":
                finish_reason = event.finish_reason or finish_reason
        text = "".join(parts).strip()
        if not text:
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型没有返回可用文本")
        return ModelChatResult(
            text=text,
            usage=usage,
            finish_reason=finish_reason,
            metadata={
                "protocol_family": self._selected_protocol_family(),
                "response_format": self._response_format,
                "fallback": "stream_completion",
            },
        )


def _extract_chat_completion(
    data: Any,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    if _looks_like_responses_completion(data):
        return _extract_responses_completion(data)
    try:
        choice = data["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型响应格式不合法") from exc
    text = _extract_chat_message_text(message)
    if not text:
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型没有返回可用文本")
    finish_reason = choice.get("finish_reason") or "stop"
    return text, finish_reason, data.get("usage") or {}, {"id": data.get("id")}


def _extract_responses_completion(
    data: Any,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    output = data.get("output")
    if not isinstance(output, list):
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "responses 响应缺少 output")
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "")
            if text:
                parts.append(text)
    text = "".join(parts).strip()
    if not text:
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型没有返回可用文本")
    return (
        text,
        str(data.get("status") or "completed"),
        data.get("usage") or {},
        {"id": data.get("id")},
    )


def _extract_chat_message_text(message: dict[str, Any]) -> str:
    candidates: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        candidates.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    candidates.append(text)
                if isinstance(item.get("content"), str):
                    candidates.append(str(item["content"]))
    for key in ("reasoning_content", "reasoning", "output_text", "text"):
        value = message.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(str(item) for item in value if isinstance(item, str))
    return "".join(part for part in candidates if str(part).strip()).strip()


def _parse_chat_stream_line(line: str) -> ModelStreamEvent | None:
    payload = _sse_payload(line)
    if payload is None:
        return None
    if payload == "[DONE]":
        return ModelStreamEvent(event="completed", finish_reason="stop")
    try:
        data = json.loads(payload)
        if _looks_like_responses_stream_event(data):
            return _parse_responses_stream_event(data)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelAdapterError(
                ErrorCode.MODEL_STREAM_SCHEMA_ERROR,
                "模型流式响应缺少 choices",
            )
        choice = choices[0]
        delta = choice.get("delta") or {}
        usage = data.get("usage") or {}
        finish_reason = choice.get("finish_reason")
    except (json.JSONDecodeError, IndexError, AttributeError, TypeError) as exc:
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型流式响应格式不合法") from exc
    if finish_reason:
        return ModelStreamEvent(event="completed", usage=usage, finish_reason=finish_reason)
    text = _extract_chat_delta_text(delta)
    if text:
        return ModelStreamEvent(event="delta", text=text, usage=usage)
    if usage:
        return ModelStreamEvent(event="usage_delta", usage=usage)
    return None


def _parse_responses_stream_line(line: str) -> ModelStreamEvent | None:
    payload = _sse_payload(line)
    if payload is None:
        return None
    if payload == "[DONE]":
        return ModelStreamEvent(event="completed", finish_reason="stop")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ModelAdapterError(
            ErrorCode.MODEL_PROTOCOL_ERROR,
            "responses 流式响应格式不合法",
        ) from exc
    return _parse_responses_stream_event(data)


def _parse_responses_stream_event(data: dict[str, Any]) -> ModelStreamEvent | None:
    event_type = str(data.get("type") or "")
    if event_type in {"response.completed", "completed"}:
        return ModelStreamEvent(
            event="completed",
            finish_reason=str(data.get("status") or "completed"),
            usage=data.get("usage") or {},
        )
    text = _extract_responses_stream_text(data)
    if text:
        return ModelStreamEvent(event="delta", text=text, usage=data.get("usage") or {})
    usage = data.get("usage") or {}
    if usage:
        return ModelStreamEvent(event="usage_delta", usage=usage)
    return None


def _extract_chat_delta_text(delta: dict[str, Any]) -> str:
    candidates: list[str] = []
    content = delta.get("content")
    if isinstance(content, str):
        candidates.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    candidates.append(text)
    for key in ("reasoning_content", "reasoning", "text"):
        value = delta.get(key)
        if isinstance(value, str):
            candidates.append(value)
    return "".join(part for part in candidates if str(part).strip()).strip()


def _extract_responses_stream_text(data: dict[str, Any]) -> str:
    candidates = [
        data.get("delta"),
        data.get("text"),
        data.get("output_text"),
    ]
    item = data.get("item")
    if isinstance(item, dict):
        candidates.extend([item.get("delta"), item.get("text")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def _sse_payload(line: str) -> str | None:
    if not line or line.startswith(":") or line.startswith("event:"):
        return None
    if not line.startswith("data:"):
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型流式响应不是 SSE data 行")
    payload = line[5:].strip()
    return payload or None


def _looks_like_responses_completion(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("output"), list)


def _looks_like_responses_stream_event(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    event_type = str(data.get("type") or "")
    return event_type.startswith("response.")


def _safe_choice(value: Any, allowed: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in allowed else None


async def _next_stream_line(
    lines: AsyncIterator[str],
    *,
    first_token_timeout_seconds: int | None,
) -> str:
    if first_token_timeout_seconds is None:
        return await anext(lines)
    try:
        return await asyncio.wait_for(
            anext(lines),
            timeout=float(first_token_timeout_seconds),
        )
    except TimeoutError as exc:
        raise ModelAdapterError(ErrorCode.MODEL_TIMEOUT, "模型首 token 超时") from exc
