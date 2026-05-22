from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from app.core.time import new_id


NormalizeEvent = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class CoalescedEventBatch:
    events: list[dict[str, Any]]
    provider_event_ids: list[str]
    attachment_count: int


class ChannelEventCoalescer:
    """Coalesce adjacent channel attachment messages into a single turn input."""

    def __init__(self, *, provider: str, normalize: NormalizeEvent) -> None:
        self._provider = provider
        self._normalize = normalize

    def coalesce(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        batches: list[CoalescedEventBatch] = []
        current: list[dict[str, Any]] = []
        current_key: tuple[str, str, str] | None = None
        for event in events:
            normalized = self._normalize(event)
            key = self._key(normalized)
            if current and (not normalized.get("attachments") or key != current_key):
                batches.append(self._batch(current))
                current = []
                current_key = None
            if normalized.get("attachments"):
                current.append(event)
                current_key = key
            else:
                if current:
                    batches.append(self._batch(current))
                    current = []
                    current_key = None
                batches.append(self._batch([event]))
        if current:
            batches.append(self._batch(current))
        return [self._materialize(batch) for batch in batches]

    def _batch(self, events: list[dict[str, Any]]) -> CoalescedEventBatch:
        ids = [str(self._normalize(event).get("provider_event_id") or "") for event in events]
        attachment_count = sum(len(self._normalize(event).get("attachments") or []) for event in events)
        return CoalescedEventBatch(events=list(events), provider_event_ids=ids, attachment_count=attachment_count)

    def _key(self, normalized: dict[str, Any]) -> tuple[str, str, str]:
        raw_event = normalized.get("raw_event") if isinstance(normalized.get("raw_event"), dict) else {}
        thread_id = (
            raw_event.get("event", {})
            .get("message", {})
            .get("thread_id", "")
            if isinstance(raw_event.get("event"), dict)
            else ""
        )
        return (
            str(normalized.get("peer_ref") or ""),
            str(normalized.get("sender_id") or ""),
            str(thread_id or ""),
        )

    def _materialize(self, batch: CoalescedEventBatch) -> dict[str, Any]:
        if len(batch.events) == 1:
            return batch.events[0]
        normalized_items = [self._normalize(event) for event in batch.events]
        first = normalized_items[0]
        attachments: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for index, item in enumerate(normalized_items, start=1):
            text = str(item.get("text") or "").strip()
            if text:
                text_parts.append(text if index == 1 else f"补充附件{index}说明：{text}")
            for attachment in item.get("attachments") or []:
                if isinstance(attachment, dict):
                    attachments.append({**attachment, "coalesced_index": index})
        synthetic_id = "coalesced:" + ",".join(batch.provider_event_ids)
        merged = {
            **first,
            "provider_event_id": synthetic_id,
            "text": "\n".join(text_parts).strip(),
            "attachments": attachments,
            "raw_event": {
                "provider": self._provider,
                "coalesced": True,
                "event_ids": batch.provider_event_ids,
                "events": batch.events,
            },
            "coalesced_event_count": len(batch.events),
        }
        return {
            "schema": "internal.coalesced_channel_event.v1",
            "header": {
                "event_id": synthetic_id or new_id("coevt"),
                "event_type": f"{self._provider}.coalesced_message",
                "create_time": str(first.get("received_at") or ""),
            },
            "event": {
                "coalesced_normalized": json.dumps(merged, ensure_ascii=False),
            },
        }

