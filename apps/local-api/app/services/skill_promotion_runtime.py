from __future__ import annotations


class SkillPromotionRuntime:
    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "skill_promotion_runtime",
            "auto_promotion_enabled": False,
            "candidate_only": True,
        }
