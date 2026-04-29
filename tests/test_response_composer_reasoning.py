from __future__ import annotations

import pytest
from response_composer import (
    ComposeRequest,
    ReasoningTagFilter,
    ResponseComposer,
    strip_reasoning_tags,
)


def test_strip_reasoning_tags_removes_closed_and_open_blocks() -> None:
    assert strip_reasoning_tags("<think>hidden</think>\n\nvisible").strip() == "visible"
    assert strip_reasoning_tags("visible\n<think>hidden").strip() == "visible"


def test_reasoning_tag_filter_hides_split_reasoning_tags() -> None:
    delta_filter = ReasoningTagFilter()

    output = "".join(
        [
            delta_filter.feed("<thi"),
            delta_filter.feed("nk>secret"),
            delta_filter.feed("</thi"),
            delta_filter.feed("nk>\n\n大脑测试成功。"),
            delta_filter.finish(),
        ]
    )

    assert output == "大脑测试成功。"


def test_reasoning_tag_filter_preserves_visible_text_around_reasoning() -> None:
    delta_filter = ReasoningTagFilter()

    output = "".join(
        [
            delta_filter.feed("前文 <think>hidden"),
            delta_filter.feed("</think> 后文"),
            delta_filter.finish(),
        ]
    )

    assert output == "前文  后文"


def test_reasoning_tag_filter_drops_unclosed_reasoning() -> None:
    delta_filter = ReasoningTagFilter()

    output = "".join(
        [
            delta_filter.feed("<think>hidden forever"),
            delta_filter.finish(),
        ]
    )

    assert output == ""


@pytest.mark.asyncio
async def test_response_composer_strips_reasoning_from_response_plan() -> None:
    result = await ResponseComposer().compose(
        ComposeRequest(user_text="", result_summary="<think>hidden</think>\n\n最终答案。")
    )

    assert result.text == "最终答案。"
    assert result.response_plan.plain_text == "最终答案。"
