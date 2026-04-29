from __future__ import annotations

from fastapi import Request

from app.services.registry import ServiceRegistry


def get_registry(request: Request) -> ServiceRegistry:
    return request.app.state.registry

