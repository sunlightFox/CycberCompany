from __future__ import annotations

from typing import Any


def artifact_names_from_refs(refs: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for ref in list(refs or []):
        if not isinstance(ref, dict):
            continue
        for key in ("name", "artifact_name", "artifact_uri", "artifact_id"):
            value = str(ref.get(key) or "").strip()
            if not value:
                continue
            names.append(value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
            break
    return _unique(names)


def summarize_completed_action_result(
    *,
    label: str = "",
    target: str = "",
    artifact_refs: list[dict[str, Any]] | None = None,
    result_summary: str = "",
) -> str:
    names = artifact_names_from_refs(artifact_refs)
    if names:
        return f"已产出文件 {'、'.join(names[:3])}"
    clean_label = str(label or "").strip()
    clean_target = str(target or "").strip()
    cleaned_result = clean_result_summary(result_summary)
    if clean_label and clean_target and clean_target not in clean_label:
        return f"{clean_label}，目标是 {clean_target}"
    if clean_label:
        return clean_label
    if clean_target:
        return clean_target
    return cleaned_result


def clean_result_summary(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    blocked_markers = (
        "已经开始推进",
        "我会按实际结果继续汇报",
        "后面如果你要继续改",
        "直接告诉我想补哪一段",
    )
    if any(marker in value for marker in blocked_markers):
        return ""
    return _truncate(value, 160)


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _truncate(text: str, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else f"{value[:limit].rstrip()}..."
