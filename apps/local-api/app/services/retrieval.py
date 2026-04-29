from __future__ import annotations

from core_types import ErrorCode

from app.core.errors import AppError
from app.db.repositories.retrieval_repo import RetrievalRepository
from app.schemas.retrieval import RetrievalDiagnosticsResponse


class RetrievalDiagnosticsService:
    def __init__(self, *, repo: RetrievalRepository) -> None:
        self._repo = repo

    async def diagnostics(self, retrieval_id: str) -> RetrievalDiagnosticsResponse:
        data = await self._repo.diagnostics(retrieval_id)
        if data is None:
            raise AppError(ErrorCode.NOT_FOUND, "检索诊断不存在", status_code=404)
        return RetrievalDiagnosticsResponse(**data)
