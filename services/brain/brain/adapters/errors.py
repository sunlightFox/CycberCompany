from __future__ import annotations

from core_types import ErrorCode


class ModelAdapterError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def map_http_status(status_code: int, message: str) -> ModelAdapterError:
    if status_code in {401, 403}:
        return ModelAdapterError(ErrorCode.MODEL_AUTH_FAILED, "模型认证失败")
    if status_code == 404:
        return ModelAdapterError(ErrorCode.MODEL_NOT_FOUND, "模型不存在或 endpoint 不支持该模型")
    if status_code == 413:
        return ModelAdapterError(ErrorCode.MODEL_CONTEXT_TOO_LARGE, "模型上下文超限")
    if status_code >= 500:
        return ModelAdapterError(ErrorCode.MODEL_UNAVAILABLE, "模型服务暂时不可用")
    return ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, message)
