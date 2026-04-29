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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    async def stream_chat(
        self,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> AsyncIterator[ModelStreamEvent]:
        if cancel_token.cancelled:
            yield ModelStreamEvent(event="cancelled", error_code=ErrorCode.TURN_CANCELLED.value)
            return

        yield ModelStreamEvent(event="started")
        payload = self._payload(request, stream=True)
        timeout = httpx.Timeout(
            timeout=request.timeout_seconds,
            connect=min(10.0, float(request.timeout_seconds)),
            read=float(request.timeout_seconds),
        )
        emitted_payload = False
        for attempt in range(request.retry_count + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    transport=self._transport,
                ) as client:
                    async with client.stream(
                        "POST",
                        self._chat_completions_url(),
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
                            event = _parse_stream_line(line)
                            if event is None:
                                continue
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
        timeout = httpx.Timeout(
            timeout=request.timeout_seconds,
            connect=min(10.0, float(request.timeout_seconds)),
            read=float(request.timeout_seconds),
        )
        response = None
        for attempt in range(request.retry_count + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                    response = await client.post(
                        self._chat_completions_url(),
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
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            finish_reason = choice.get("finish_reason") or "stop"
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型响应格式不合法") from exc
        return ModelChatResult(
            text=text,
            usage=data.get("usage") or {},
            finish_reason=finish_reason,
            metadata={"id": data.get("id")},
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _chat_completions_url(self) -> str:
        if self._endpoint.endswith("/chat/completions"):
            return self._endpoint
        if self._endpoint.endswith("/v1"):
            return f"{self._endpoint}/chat/completions"
        return f"{self._endpoint}/v1/chat/completions"

    def _payload(self, request: ModelChatRequest, *, stream: bool) -> dict[str, Any]:
        return {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "max_tokens": request.max_output_tokens,
            "stream": stream,
        }


def _parse_stream_line(line: str) -> ModelStreamEvent | None:
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型流式响应不是 SSE data 行")
    payload = line[5:].strip()
    if not payload:
        return None
    if payload == "[DONE]":
        return ModelStreamEvent(event="completed", finish_reason="stop")
    try:
        data = json.loads(payload)
        choice = data.get("choices", [{}])[0]
        delta = choice.get("delta") or {}
        text = delta.get("content") or ""
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage") or {}
    except (json.JSONDecodeError, IndexError, AttributeError) as exc:
        raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型流式响应格式不合法") from exc
    if finish_reason:
        return ModelStreamEvent(event="completed", usage=usage, finish_reason=finish_reason)
    if text:
        return ModelStreamEvent(event="delta", text=text, usage=usage)
    if usage:
        return ModelStreamEvent(event="usage_delta", usage=usage)
    return None


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
