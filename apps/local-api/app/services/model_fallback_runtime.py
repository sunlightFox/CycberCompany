from __future__ import annotations

from typing import Any


class ModelFallbackRuntime:
    def candidate_chain(
        self,
        *,
        primary_brain_id: str,
        fallback_brain_ids: list[str],
    ) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for brain_id in [primary_brain_id, *fallback_brain_ids]:
            if brain_id and brain_id not in seen:
                ordered.append(brain_id)
                seen.add(brain_id)
        return ordered

    def diagnostic(
        self,
        *,
        primary_brain_id: str,
        fallback_brain_ids: list[str],
    ) -> dict[str, Any]:
        chain = self.candidate_chain(
            primary_brain_id=primary_brain_id,
            fallback_brain_ids=fallback_brain_ids,
        )
        return {
            "primary_brain_id": primary_brain_id,
            "fallback_chain": chain[1:],
            "candidate_count": len(chain),
        }
