from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_SOURCE_DIRS = [
    ROOT / "apps/local-api/app/services",
    ROOT / "services/response-composer/response_composer",
]
FORBIDDEN_RUNTIME_PHRASES = (
    "好的，我来",
    "我来继续",
    "记住了。",
    "处理结果如下",
    "当前状态报告",
    "作为 AI",
)


def test_phase65_runtime_text_does_not_ship_mechanical_openers() -> None:
    offenders: list[str] = []
    for source_dir in RUNTIME_SOURCE_DIRS:
        for path in source_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for phrase in FORBIDDEN_RUNTIME_PHRASES:
                if phrase in text:
                    offenders.append(f"{path.relative_to(ROOT)}::{phrase}")
    assert offenders == []


def test_phase65_catalog_metadata_covers_required_scenarios() -> None:
    from response_composer.opening_copy import voice_catalog_metadata

    metadata = voice_catalog_metadata()

    assert metadata["coverage"] == 1.0
    assert metadata["required_scenarios"] == metadata["covered_scenarios"]


def test_phase65_channel_session_context_stays_metadata_only() -> None:
    text = (
        ROOT / "apps/local-api/app/services/channel_session_context.py"
    ).read_text(encoding="utf-8")

    assert "plain_text" not in text
    assert "opening_copy" not in text
    assert "ResponseComposer" not in text
