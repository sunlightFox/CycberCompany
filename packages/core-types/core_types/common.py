from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

EntityId = str
Timestamp = datetime


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ErrorPayload(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None


class ErrorEnvelope(ApiModel):
    error: ErrorPayload

