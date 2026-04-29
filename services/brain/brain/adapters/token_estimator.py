from __future__ import annotations


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    non_cjk = len(text) - cjk_chars
    return max(1, cjk_chars + non_cjk // 4)


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_text_tokens(message.get("content", "")) + 4 for message in messages)
