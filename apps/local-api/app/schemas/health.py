from __future__ import annotations

from core_types import ApiModel


class HealthResponse(ApiModel):
    status: str
    db: str
    default_shell: str
    version: str
    trace_id: str | None = None

