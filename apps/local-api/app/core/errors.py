from __future__ import annotations

from typing import Any

from core_types import ErrorCode, ErrorEnvelope, ErrorPayload
from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from trace_service import redact


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, ErrorCode) else code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code=ErrorCode.VALIDATION_ERROR.value,
        message="请求参数不合法",
        details={"errors": redact(exc.errors())},
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = ErrorCode.NOT_FOUND.value if exc.status_code == 404 else ErrorCode.INTERNAL_ERROR.value
    message = "资源不存在" if exc.status_code == 404 else "请求处理失败"
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        details={},
    )


async def config_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=ErrorCode.CONFIG_ERROR.value,
        message="运行时配置加载失败",
        details={"reason": redact(str(exc))},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=ErrorCode.INTERNAL_ERROR.value,
        message="服务内部错误",
        details={},
    )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    envelope = ErrorEnvelope(
        error=ErrorPayload(
            code=code,
            message=message,
            details=redact(details),
            trace_id=trace_id,
        )
    )
    return JSONResponse(status_code=status_code, content=jsonable_encoder(envelope))
