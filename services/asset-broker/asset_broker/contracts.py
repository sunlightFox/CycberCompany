from __future__ import annotations

from core_types import ApiModel, AssetHandle, AssetQuery, EntityId


class ResolvedAsset(ApiModel):
    handle_id: EntityId
    asset_id: EntityId
    action: str
    secret_ref: str | None = None


class AssetBroker:
    async def query(self, query: AssetQuery) -> list[AssetHandle]:
        raise NotImplementedError("AssetBroker contract requires an application implementation")

    async def resolve_for_tool(self, handle_id: EntityId, action: str) -> ResolvedAsset:
        raise NotImplementedError("AssetBroker contract requires an application implementation")
