from __future__ import annotations

from typing import Any


class SkillCandidateExtractor:
    def extract_from_replay(self, replay: Any) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        tool_calls = list(getattr(replay, "tool_calls", []) or [])
        if len(tool_calls) >= 2:
            candidates.append(
                {
                    "candidate_type": "tool_chain",
                    "source": "task_replay",
                    "tool_names": [call.tool_name for call in tool_calls[:5]],
                    "confidence": "low",
                }
            )
        return candidates
