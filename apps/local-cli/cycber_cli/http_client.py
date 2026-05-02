from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from cycber_cli.redaction import redact
from cycber_cli.sse import SSEDecoder


class ApiError(RuntimeError):
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.payload = redact(payload)
        super().__init__(f"API request failed: {status_code} {self.payload}")


class CycberApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CycberApiClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def get_json(self, path: str) -> dict[str, Any]:
        response = await self._client.get(path)
        return self._json_or_error(response)

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(path, json=payload)
        return self._json_or_error(response)

    async def health(self) -> dict[str, Any]:
        return await self.get_json("/health")

    async def full_health(self) -> dict[str, Any]:
        return await self.get_json("/api/health/full")

    async def runtime_contracts(self) -> dict[str, Any]:
        return await self.get_json("/api/system/runtime-contracts")

    async def conversations(self) -> list[dict[str, Any]]:
        return list((await self.get_json("/api/chat/conversations")).get("items", []))

    async def conversation(self, conversation_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/chat/conversations/{conversation_id}")

    async def create_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/chat/turn", payload)

    async def turn(self, turn_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/chat/turns/{turn_id}")

    async def turn_events(self, turn_id: str) -> list[dict[str, Any]]:
        return list((await self.get_json(f"/api/chat/turns/{turn_id}/events")).get("items", []))

    async def brain_decision(self, turn_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/chat/turns/{turn_id}/brain-decision")

    async def semantic_review(self, turn_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/chat/turns/{turn_id}/semantic-review")

    async def tone_policy(self, turn_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/chat/turns/{turn_id}/tone-policy")

    async def response_quality(self, turn_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/chat/turns/{turn_id}/response-quality")

    async def trace(self, trace_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/traces/{trace_id}")

    async def skill_repositories(self) -> dict[str, Any]:
        return await self.get_json("/api/skills/repositories")

    async def refresh_skill_repository(self, repository_id: str) -> dict[str, Any]:
        return await self.post_json(f"/api/skills/repositories/{repository_id}/refresh", {})

    async def upsert_skill_repository(
        self,
        repository_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.put(f"/api/skills/repositories/{repository_id}", json=payload)
        return self._json_or_error(response)

    async def disable_skill_repository(self, repository_id: str) -> dict[str, Any]:
        response = await self._client.delete(f"/api/skills/repositories/{repository_id}")
        return self._json_or_error(response)

    async def search_skills(
        self,
        query: str,
        *,
        repository_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query, "limit": limit}
        if repository_id:
            params["repository_id"] = repository_id
        response = await self._client.get("/api/skills/catalog/search", params=params)
        return self._json_or_error(response)

    async def preview_skill_install(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/skills/preview-install", payload)

    async def install_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/skills/install", payload)

    async def enable_plugin(
        self,
        bundle_id: str,
        actor_member_id: str = "mem_xiaoyao",
    ) -> dict[str, Any]:
        return await self.post_json(
            f"/api/plugins/{bundle_id}/enable",
            {"actor_member_id": actor_member_id},
        )

    async def grant_skill(
        self,
        skill_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.post_json(f"/api/skills/{skill_id}/grants", payload)

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/tasks", payload)

    async def start_task(self, task_id: str) -> dict[str, Any]:
        return await self.post_json(f"/api/tasks/{task_id}/start", {})

    async def task_artifacts(self, task_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/tasks/{task_id}/artifacts")

    async def download_artifact(self, artifact_id: str) -> tuple[bytes, dict[str, str]]:
        response = await self._client.get(f"/api/artifacts/{artifact_id}/download")
        if response.status_code >= 400:
            try:
                payload: Any = response.json()
            except json.JSONDecodeError:
                payload = {"text": response.text}
            raise ApiError(response.status_code, payload)
        return response.content, dict(response.headers)

    async def stream_turn(self, turn_id: str) -> AsyncIterator[dict[str, Any]]:
        decoder = SSEDecoder()
        async with self._client.stream("GET", f"/api/chat/stream/{turn_id}") as response:
            if response.status_code >= 400:
                raise ApiError(response.status_code, await response.aread())
            async for line in response.aiter_lines():
                for event in decoder.feed_line(line):
                    yield _event_payload(event.event, event.data)
        for event in decoder.close():
            yield _event_payload(event.event, event.data)

    def _json_or_error(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload: Any = response.json()
        except json.JSONDecodeError:
            payload = {"text": response.text}
        if response.status_code >= 400:
            raise ApiError(response.status_code, payload)
        if not isinstance(payload, dict):
            return {"value": payload}
        return payload


def _event_payload(event_name: str, data: str) -> dict[str, Any]:
    try:
        payload = json.loads(data) if data else {}
    except json.JSONDecodeError:
        payload = {"data": data}
    if isinstance(payload, dict):
        return {"event": event_name, **payload}
    return {"event": event_name, "data": payload}
