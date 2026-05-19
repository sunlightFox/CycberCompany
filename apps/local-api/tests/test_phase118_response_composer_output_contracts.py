from __future__ import annotations

import pytest
from response_composer import ComposeRequest, ResponseComposer


@pytest.mark.asyncio
async def test_phase118_response_composer_strips_untrusted_done_preface() -> None:
    result = await ResponseComposer().compose(
        ComposeRequest(
            user_text="\u7528\u4e00\u53e5\u8bdd\u7b80\u5355\u8bf4\u660e",
            result_summary="\u5f53\u524d\u7ed3\u679c\u662f\uff1a\u8fd9\u662f\u6700\u7ec8\u7b80\u4ecb\u3002",
            response_policy={
                "opening_style": "natural",
                "depth_mode": "light",
                "followthrough_mode": "standalone",
                "boundary_mode": "none",
                "progress_mode": "answer_directly",
                "structure_mode": "minimal",
            },
        )
    )

    assert "\u5f53\u524d\u7ed3\u679c\u662f" not in result.text
    assert "\u8fd9\u662f\u6700\u7ec8\u7b80\u4ecb" in result.text


@pytest.mark.asyncio
async def test_phase118_response_composer_honors_json_only_request() -> None:
    result = await ResponseComposer().compose(
        ComposeRequest(
            user_text="\u53ea\u8f93\u51fa JSON",
            result_summary="Result:\n{\"ok\": true, \"source\": \"composer\"}",
            response_policy={
                "opening_style": "natural",
                "depth_mode": "light",
                "followthrough_mode": "standalone",
                "boundary_mode": "none",
                "progress_mode": "answer_directly",
                "structure_mode": "minimal",
            },
        )
    )

    assert result.text == "{\"ok\": true, \"source\": \"composer\"}"


@pytest.mark.asyncio
async def test_phase118_response_composer_honors_three_line_request() -> None:
    result = await ResponseComposer().compose(
        ComposeRequest(
            user_text="\u4e09\u884c\u5185\u56de\u7b54",
            result_summary=(
                "\u7b2c\u4e00\u884c\u8bf4\u80cc\u666f\u3002\n"
                "\u7b2c\u4e8c\u884c\u8bf4\u98ce\u9669\u3002\n"
                "\u7b2c\u4e09\u884c\u8bf4\u5efa\u8bae\u3002\n"
                "\u7b2c\u56db\u884c\u8bf4\u8ddf\u8fdb\u3002"
            ),
            response_policy={
                "opening_style": "natural",
                "depth_mode": "deep",
                "followthrough_mode": "standalone",
                "boundary_mode": "none",
                "progress_mode": "answer_then_expand",
                "structure_mode": "structured_when_useful",
            },
        )
    )

    non_empty_lines = [line for line in result.text.splitlines() if line.strip()]
    assert len(non_empty_lines) <= 3
    assert "\u7b2c\u56db\u884c" in result.text
