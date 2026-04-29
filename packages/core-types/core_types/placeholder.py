from __future__ import annotations

from core_types.common import ApiModel
from core_types.enums import CapabilityStatus


class CapabilityPlaceholder(ApiModel):
    capability_status: CapabilityStatus = CapabilityStatus.NOT_IMPLEMENTED
    available: bool = False
    reason: str
    trace_id: str | None = None

