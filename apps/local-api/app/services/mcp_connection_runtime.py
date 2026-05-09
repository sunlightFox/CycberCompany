from __future__ import annotations

from typing import Any


class MCPConnectionRuntime:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def diagnostic(self) -> dict[str, Any]:
        servers = await self._service.list_servers()
        return {
            "runtime": "mcp_connection_runtime",
            "server_count": len(servers),
            "ready_servers": len([item for item in servers if item.status == "ready"]),
        }
