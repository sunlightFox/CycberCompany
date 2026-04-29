from __future__ import annotations

from core_types import ApiModel


class HeartSignal(ApiModel):
    warmth: float = 0.6
    humor: float = 0.15
    directness: float = 0.75
    urgency: str = "normal"
    mood: str = "steady"
    user_state: str = "steady"
    preferred_pace: str = "normal"
    relationship_temperature: float = 0.6
    companionship_level: float = 0.5
    deescalation_required: bool = False
    risk_tone_override: str | None = None
    confidence: float = 0.6
    reason: str = "deterministic_heart_policy"


class HeartService:
    async def evaluate(self, text: str) -> HeartSignal:
        lowered = text.lower()
        urgent = any(word in lowered for word in ["urgent", "紧急", "马上", "立刻"])
        anxious = any(word in lowered for word in ["panic", "焦虑", "慌", "担心"])
        angry = any(word in lowered for word in ["angry", "生气", "愤怒", "火大"])
        happy = any(word in lowered for word in ["happy", "开心", "太好了", "nice"])
        high_risk = any(word in lowered for word in ["删除", "转账", "支付", "签名", "delete"])
        tense = anxious or angry
        return HeartSignal(
            warmth=0.78 if tense else 0.62,
            humor=0.05 if tense else 0.12,
            directness=0.82 if urgent else 0.74,
            urgency="high" if urgent else "normal",
            mood="angry" if angry else "anxious" if anxious else "positive" if happy else "steady",
            user_state=(
                "needs_deescalation"
                if angry
                else "needs_reassurance"
                if anxious
                else "time_sensitive"
                if urgent
                else "energized"
                if happy
                else "steady"
            ),
            preferred_pace=(
                "slow_and_clear"
                if angry
                else "step_by_step"
                if anxious
                else "concise"
                if urgent
                else "normal"
            ),
            relationship_temperature=0.72 if tense else 0.64,
            companionship_level=0.62 if tense else 0.52,
            deescalation_required=angry or (high_risk and (urgent or anxious)),
            risk_tone_override="clear_and_calm" if high_risk else None,
            confidence=0.8 if tense or urgent else 0.65,
            reason="text_rules",
        )
