from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SOUL_FILE_NAME = "SOUL.md"
CANONICAL_SECTIONS = (
    "identity",
    "voice",
    "work_style",
    "boundaries",
    "memory_policy",
    "catchphrases",
    "custom_notes",
)
CANONICAL_TITLES = {
    "identity": "Identity",
    "voice": "Voice",
    "work_style": "Work Style",
    "boundaries": "Boundaries",
    "memory_policy": "Memory Policy",
    "catchphrases": "Catchphrases",
    "custom_notes": "Custom Notes",
}
SECTION_ALIASES = {
    "identity": "identity",
    "身份": "identity",
    "自我": "identity",
    "voice": "voice",
    "tone": "voice",
    "语气": "voice",
    "口吻": "voice",
    "说话方式": "voice",
    "workstyle": "work_style",
    "work_style": "work_style",
    "工作方式": "work_style",
    "做事方式": "work_style",
    "boundaries": "boundaries",
    "boundary": "boundaries",
    "边界": "boundaries",
    "安全边界": "boundaries",
    "memorypolicy": "memory_policy",
    "memory_policy": "memory_policy",
    "记忆策略": "memory_policy",
    "记忆规则": "memory_policy",
    "catchphrases": "catchphrases",
    "catchphrase": "catchphrases",
    "口头禅": "catchphrases",
    "常用表达": "catchphrases",
    "customnotes": "custom_notes",
    "custom_notes": "custom_notes",
    "自定义备注": "custom_notes",
    "补充": "custom_notes",
}

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
_NEGATION_MARKERS = (
    "不能",
    "不要",
    "不得",
    "不会",
    "不可",
    "禁止",
    "严禁",
    "避免",
    "没有",
    "并无",
    "无",
    "不存在",
    "别",
    "不",
    "do not",
    "don't",
    "must not",
    "never",
    "cannot",
    "can't",
    "avoid",
)
_UNSAFE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "approval_bypass",
        re.compile(
            r"绕过\s*审批|跳过\s*审批|无需\s*审批|不需要\s*审批|"
            r"bypass\s+approval|skip\s+approval|approval\s+bypass",
            re.IGNORECASE,
        ),
    ),
    (
        "permission_bypass",
        re.compile(
            r"绕过\s*权限|越过\s*权限|权限\s*绕过|"
            r"bypass\s+permission|permission\s+override|capability\s+override",
            re.IGNORECASE,
        ),
    ),
    (
        "capability_boundary_bypass",
        re.compile(
            r"绕过\s*(?:系统\s*)?能力\s*边界|跳过\s*(?:系统\s*)?能力\s*边界|"
            r"无需\s*(?:系统\s*)?能力\s*边界|不需要\s*(?:系统\s*)?能力\s*边界|"
            r"bypass\s+(?:system\s+)?capability\s+boundar(?:y|ies)",
            re.IGNORECASE,
        ),
    ),
    (
        "human_impersonation",
        re.compile(
            r"(?:我|你|小吴|助手|agent)?\s*(?:是|就是|作为)\s*(?:现实\s*)?真人|"
            r"真人\s*客服|真人\s*操作员|"
            r"real\s+(?:person|human)|human\s+operator",
            re.IGNORECASE,
        ),
    ),
    (
        "hidden_account_claim",
        re.compile(
            r"隐藏\s*账号|隐藏\s*账户|后门\s*账号|后门\s*账户|隐秘\s*账号|"
            r"hidden\s+account|backdoor\s+account|shadow\s+account",
            re.IGNORECASE,
        ),
    ),
    (
        "asset_grant",
        re.compile(
            r"使用\s*所有\s*资产|读取\s*所有\s*资产|全部\s*资产\s*可用|"
            r"all\s+assets|asset\s+grant",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_reference",
        re.compile(
            r"secret|token|private[_\s-]?key|api[_\s-]?key|cookie|password|私钥|密钥",
            re.IGNORECASE,
        ),
    ),
    (
        "fake_execution",
        re.compile(
            r"已经?执行|执行完成|已经?完成操作|已经?删除|已经?安装|已经?下载|"
            r"假装执行|fake\s+execution|pretend(?:ed)?\s+to\s+execute",
            re.IGNORECASE,
        ),
    ),
    (
        "tool_overclaim",
        re.compile(
            r"无需工具.*执行|不用工具.*执行|可以直接执行所有工具|can\s+execute\s+all\s+tools",
            re.IGNORECASE,
        ),
    ),
)
_UNSAFE_KEY_RE = re.compile(
    r"approval_override|asset_grant|bypass|secret|token|private[_-]?key|"
    r"api[_-]?key|permission_override|role_override|safety_override|can_execute|"
    r"hidden[_-]?account|backdoor[_-]?account|human[_-]?identity|real[_-]?person|"
    r"capability[_-]?boundary[_-]?bypass",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SoulSection:
    title: str
    normalized_title: str
    content: str
    level: int = 1


@dataclass(frozen=True)
class SoulDocument:
    frontmatter: dict[str, Any]
    sections: list[SoulSection]
    preamble: str = ""
    frontmatter_error: str | None = None


def soul_manifest_path(data_dir: Path, member_id: str) -> Path:
    return Path(data_dir) / "personas" / str(member_id) / SOUL_FILE_NAME


def normalize_soul_content(content: str) -> str:
    return str(content or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def soul_content_hash(content: str) -> str:
    normalized = normalize_soul_content(content)
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_soul_markdown(content: str) -> SoulDocument:
    normalized = normalize_soul_content(content)
    frontmatter: dict[str, Any] = {}
    frontmatter_error: str | None = None
    body = normalized
    lines = normalized.splitlines()
    if lines and lines[0].strip() == "---":
        end_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() in {"---", "..."}:
                end_index = index
                break
        if end_index is not None:
            raw_frontmatter = "\n".join(lines[1:end_index])
            body = "\n".join(lines[end_index + 1 :])
            try:
                parsed = yaml.safe_load(raw_frontmatter) or {}
                if isinstance(parsed, dict):
                    frontmatter = parsed
                else:
                    frontmatter_error = "YAML frontmatter must be a mapping."
            except yaml.YAMLError as exc:
                frontmatter_error = str(exc)

    sections: list[SoulSection] = []
    preamble: list[str] = []
    current_title: str | None = None
    current_level = 1
    current_content: list[str] = []
    for line in body.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            if current_title is None:
                if current_content:
                    preamble.extend(current_content)
            else:
                sections.append(
                    SoulSection(
                        title=current_title,
                        normalized_title=normalize_section_title(current_title),
                        content="\n".join(current_content).strip(),
                        level=current_level,
                    )
                )
            current_title = heading.group(2).strip().strip(":")
            current_level = len(heading.group(1))
            current_content = []
            continue
        current_content.append(line)
    if current_title is None:
        preamble.extend(current_content)
    else:
        sections.append(
            SoulSection(
                title=current_title,
                normalized_title=normalize_section_title(current_title),
                content="\n".join(current_content).strip(),
                level=current_level,
            )
        )
    preamble_text = "\n".join(preamble).strip()
    if preamble_text and not sections:
        sections.append(
            SoulSection(
                title=CANONICAL_TITLES["custom_notes"],
                normalized_title="custom_notes",
                content=preamble_text,
                level=1,
            )
        )
        preamble_text = ""
    return SoulDocument(
        frontmatter=frontmatter,
        sections=sections,
        preamble=preamble_text,
        frontmatter_error=frontmatter_error,
    )


def normalize_section_title(title: str) -> str:
    key = re.sub(r"[\s\-:/：_]+", "", str(title or "").strip().lower())
    if key in SECTION_ALIASES:
        return SECTION_ALIASES[key]
    raw = str(title or "").strip().lower().replace("-", "_").replace(" ", "_")
    return SECTION_ALIASES.get(raw, raw)


def soul_section(document: SoulDocument, section_name: str) -> SoulSection | None:
    normalized = normalize_section_title(section_name)
    for section in document.sections:
        if section.normalized_title == normalized:
            return section
    return None


def validate_soul_document(document: SoulDocument) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if document.frontmatter_error:
        issues.append(
            {
                "code": "frontmatter_invalid",
                "severity": "error",
                "section": "frontmatter",
                "message": "YAML frontmatter 无法解析为对象。",
                "snippet": document.frontmatter_error[:180],
            }
        )
    _scan_value(
        document.frontmatter,
        section="frontmatter",
        issues=issues,
    )
    for section in document.sections:
        for line in section.content.splitlines():
            for code, match in _unsafe_matches(line):
                issues.append(
                    {
                        "code": code,
                        "severity": "blocked",
                        "section": section.title,
                        "message": "SOUL.md 不能授予权限、绕过审批、暴露密钥或伪造执行能力。",
                        "snippet": _snippet(match.group(0) or line),
                    }
                )
    return _dedupe_issues(issues)


def validation_status(issues: list[dict[str, Any]]) -> str:
    severities = {str(issue.get("severity") or "") for issue in issues}
    if "error" in severities:
        return "invalid"
    if "blocked" in severities:
        return "blocked"
    if issues:
        return "warning"
    return "valid"


def compile_soul_markdown(
    *,
    member: dict[str, Any],
    profile: dict[str, Any],
    consistency: dict[str, Any],
    content: str,
    source: str,
) -> dict[str, Any]:
    del source
    normalized_content = normalize_soul_content(content)
    document = parse_soul_markdown(normalized_content)
    issues = validate_soul_document(document)
    safe_frontmatter = _sanitize_frontmatter(document.frontmatter)
    display_name = str(
        safe_frontmatter.get("display_name")
        or member.get("display_name")
        or profile.get("display_name")
        or "当前成员"
    )
    member_id = str(member.get("member_id") or profile.get("member_id") or "")
    profile_id = str(
        safe_frontmatter.get("persona_profile_id")
        or profile.get("persona_profile_id")
        or member.get("persona_profile_id")
        or f"persona_{member_id or 'default'}"
    )
    identity_text = _first_text(
        _safe_section_text(document, "identity"),
        str(safe_frontmatter.get("summary") or ""),
        _safe_plain_text(profile.get("summary")),
        f"{display_name} 是当前聊天对象，保持可靠、直接、温暖。",
    )
    voice_items = _section_items(document, "voice")
    work_items = _section_items(document, "work_style")
    boundary_items = _section_items(document, "boundaries")
    memory_items = _section_items(document, "memory_policy")
    catchphrases = _section_items(document, "catchphrases")
    if not catchphrases:
        catchphrases = _safe_list(safe_frontmatter.get("catchphrases"))
    custom_notes = _safe_section_text(document, "custom_notes")
    custom_sections = _custom_sections(document)

    tone_policy = _compile_tone_policy(
        dict(profile.get("tone_policy") or {}),
        safe_frontmatter.get("tone_policy"),
        voice_items,
        catchphrases,
    )
    disclosure_policy = _safe_mapping(
        safe_frontmatter.get("disclosure_policy") or profile.get("disclosure_policy") or {}
    )
    risk_tone_policy = _safe_mapping(
        safe_frontmatter.get("risk_tone_policy") or profile.get("risk_tone_policy") or {}
    )
    allowed_modes = _safe_list(
        safe_frontmatter.get("allowed_modes") or profile.get("allowed_modes") or []
    )
    default_mode = str(safe_frontmatter.get("default_mode") or profile.get("default_mode") or "")
    if not default_mode:
        default_mode = "playful_witty" if tone_policy.get("humor", 0) >= 0.55 else "default"
    if default_mode not in allowed_modes:
        allowed_modes = _merge_unique([default_mode], allowed_modes, ["default"])

    style_principles = _merge_unique(
        work_items,
        voice_items,
        _safe_list(consistency.get("style_principles")),
        limit=16,
    )
    forbidden_claims = _merge_unique(
        boundary_items,
        _safe_list(consistency.get("forbidden_claims")),
        limit=20,
    )
    mode_switch_rules = _safe_rules(
        safe_frontmatter.get("mode_switch_rules") or consistency.get("mode_switch_rules") or []
    )
    consistency_markers = _merge_unique(
        _safe_list(consistency.get("consistency_markers")),
        ["soul_manifest_compiled"],
        limit=12,
    )
    disabled_patterns = _merge_unique(
        _safe_list(consistency.get("disabled_patterns")),
        limit=12,
    )
    summary = _compile_summary(
        safe_frontmatter.get("summary"),
        identity_text,
        voice_items,
        work_items,
        catchphrases,
    )
    compiled_snapshot = {
        "member_id": member_id,
        "persona_profile_id": profile_id,
        "display_name": display_name,
        "summary": summary,
        "identity": identity_text,
        "voice": {
            "text": _safe_section_text(document, "voice"),
            "items": voice_items,
        },
        "work_style": {
            "text": _safe_section_text(document, "work_style"),
            "items": work_items,
        },
        "boundaries": {
            "text": _safe_section_text(document, "boundaries"),
            "items": boundary_items,
        },
        "memory_policy": {
            "text": _safe_section_text(document, "memory_policy"),
            "items": memory_items,
        },
        "catchphrases": catchphrases,
        "custom_notes": {
            "text": custom_notes,
            "items": _items_from_text(custom_notes),
        },
        "custom_sections": custom_sections,
        "tone_policy": tone_policy,
        "disclosure_policy": disclosure_policy,
        "risk_tone_policy": risk_tone_policy,
        "allowed_modes": allowed_modes,
        "default_mode": default_mode,
        "style_principles": style_principles,
        "forbidden_claims": forbidden_claims,
        "mode_switch_rules": mode_switch_rules,
        "consistency_markers": consistency_markers,
        "disabled_patterns": disabled_patterns,
        "frontmatter": safe_frontmatter,
    }
    return {
        "content_hash": soul_content_hash(normalized_content),
        "validation_status": validation_status(issues),
        "validation_errors": issues,
        "compiled_snapshot": compiled_snapshot,
        "profile_fields": {
            "persona_profile_id": profile_id,
            "organization_id": member.get("organization_id")
            or profile.get("organization_id")
            or "org_default",
            "member_id": member_id or profile.get("member_id"),
            "display_name": display_name,
            "summary": str(profile.get("summary") or summary),
            "tone_policy": tone_policy,
            "disclosure_policy": disclosure_policy,
            "risk_tone_policy": risk_tone_policy,
            "allowed_modes": allowed_modes,
            "default_mode": default_mode,
            "shell_label_mapping": dict(profile.get("shell_label_mapping") or {}),
            "status": str(profile.get("status") or "active"),
        },
        "consistency_fields": {
            "persona_profile_id": profile_id,
            "member_id": member_id or profile.get("member_id"),
            "style_principles": style_principles,
            "forbidden_claims": forbidden_claims,
            "mode_switch_rules": mode_switch_rules,
            "consistency_markers": consistency_markers,
            "disabled_patterns": disabled_patterns,
            "source": "soul_manifest_compiled",
            "status": "active",
        },
    }


def render_soul_markdown(
    *,
    member: dict[str, Any],
    profile: dict[str, Any],
    consistency: dict[str, Any],
    catchphrases: list[str] | None = None,
    custom_sections: list[dict[str, Any]] | None = None,
    custom_notes: str | None = None,
) -> str:
    member_id = str(member.get("member_id") or profile.get("member_id") or "")
    persona_key = _persona_seed_key(member, profile)
    profile_id = str(profile.get("persona_profile_id") or member.get("persona_profile_id") or "")
    display_name = str(member.get("display_name") or profile.get("display_name") or "当前成员")
    tone_policy = dict(profile.get("tone_policy") or {})
    frontmatter = {
        "member_id": member_id,
        "persona_profile_id": profile_id,
        "display_name": display_name,
        "default_mode": profile.get("default_mode") or "default",
        "allowed_modes": profile.get("allowed_modes") or ["default"],
        "tone_policy": tone_policy,
        "source": "soul_manifest",
    }
    identity = str(profile.get("summary") or f"{display_name} 保持可靠、直接、温暖。")
    voice = _voice_seed_lines(member_id, tone_policy, persona_key=persona_key)
    work_style = _safe_list(consistency.get("style_principles")) or _default_work_style_lines(
        persona_key
    )
    boundaries = _boundary_seed_lines(consistency)
    memory_policy = _memory_policy_lines(member)
    final_catchphrases = catchphrases or _default_catchphrases(member_id, persona_key=persona_key)
    final_custom_notes = custom_notes or "可以在这里补充长期沟通偏好、口头禅或习惯。"

    sections: list[tuple[str, str | list[str]]] = [
        (CANONICAL_TITLES["identity"], identity),
        (CANONICAL_TITLES["voice"], voice),
        (CANONICAL_TITLES["work_style"], work_style),
        (CANONICAL_TITLES["boundaries"], boundaries),
        (CANONICAL_TITLES["memory_policy"], memory_policy),
        (CANONICAL_TITLES["catchphrases"], final_catchphrases),
        (CANONICAL_TITLES["custom_notes"], final_custom_notes),
    ]
    rendered = ["---", yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip(), "---", ""]
    for title, body in sections:
        rendered.append(f"# {title}")
        rendered.append(_render_body(body))
        rendered.append("")
    for section in custom_sections or []:
        title = str(section.get("title") or "").strip()
        content = str(section.get("content") or "").strip()
        if not title or normalize_section_title(title) in CANONICAL_SECTIONS:
            continue
        rendered.append(f"# {title}")
        rendered.append(content)
        rendered.append("")
    return "\n".join(rendered).rstrip() + "\n"


def existing_custom_sections(content: str) -> list[dict[str, str]]:
    document = parse_soul_markdown(content)
    return _custom_sections(document)


def existing_catchphrases(content: str) -> list[str]:
    document = parse_soul_markdown(content)
    return _section_items(document, "catchphrases")


def existing_custom_notes(content: str) -> str:
    document = parse_soul_markdown(content)
    return _safe_section_text(document, "custom_notes")


def _safe_section_text(document: SoulDocument, section_name: str) -> str:
    normalized = normalize_section_title(section_name)
    sections = [
        section
        for section in document.sections
        if section.normalized_title == normalized
    ]
    if not sections:
        return ""
    lines: list[str] = []
    for section in sections:
        lines.extend(
            line
            for line in section.content.splitlines()
            if not any(True for _code, _match in _unsafe_matches(line))
        )
    return "\n".join(lines).strip()


def _section_items(document: SoulDocument, section_name: str) -> list[str]:
    return _items_from_text(_safe_section_text(document, section_name))


def _items_from_text(text: str) -> list[str]:
    items: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet = _BULLET_RE.match(line)
        item = bullet.group(1).strip() if bullet else line
        if not item:
            continue
        if "；" in item and len(item) > 48:
            parts = [part.strip() for part in item.split("；") if part.strip()]
        elif ";" in item and len(item) > 48:
            parts = [part.strip() for part in item.split(";") if part.strip()]
        else:
            parts = [item]
        for part in parts:
            if part and part not in items:
                items.append(part[:180])
    return items[:16]


def _custom_sections(document: SoulDocument) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for section in document.sections:
        if section.normalized_title in CANONICAL_SECTIONS:
            continue
        safe_content = "\n".join(
            line
            for line in section.content.splitlines()
            if not any(True for _code, _match in _unsafe_matches(line))
        ).strip()
        if safe_content:
            result.append({"title": section.title, "content": safe_content[:1200]})
    return result[:8]


def _scan_value(value: Any, *, section: str, issues: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            if _UNSAFE_KEY_RE.search(key_text):
                issues.append(
                    {
                        "code": "unsafe_frontmatter_key",
                        "severity": "blocked",
                        "section": section,
                        "message": "SOUL.md frontmatter 不能声明权限、密钥或执行豁免。",
                        "snippet": key_text[:120],
                    }
                )
            _scan_value(nested, section=section, issues=issues)
        return
    if isinstance(value, list):
        for item in value:
            _scan_value(item, section=section, issues=issues)
        return
    if isinstance(value, str):
        for code, match in _unsafe_matches(value):
            issues.append(
                {
                    "code": code,
                    "severity": "blocked",
                    "section": section,
                    "message": "SOUL.md frontmatter 不能包含危险能力声明或敏感凭据。",
                    "snippet": _snippet(match.group(0) or value),
                }
            )


def _unsafe_matches(line: str) -> list[tuple[str, re.Match[str]]]:
    text = str(line or "")
    hits: list[tuple[str, re.Match[str]]] = []
    for code, pattern in _UNSAFE_PATTERNS:
        for match in pattern.finditer(text):
            if _is_negated(text, match.start()):
                continue
            hits.append((code, match))
    return hits


def _is_negated(text: str, match_start: int) -> bool:
    before = text[max(0, match_start - 32) : match_start].lower()
    return any(marker in before for marker in _NEGATION_MARKERS)


def _sanitize_frontmatter(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_value(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if _UNSAFE_KEY_RE.search(str(key)):
                continue
            sanitized = _sanitize_value(nested)
            if sanitized is not None:
                result[str(key)] = sanitized
        return result
    if isinstance(value, list):
        result = [_sanitize_value(item) for item in value]
        return [item for item in result if item is not None]
    if isinstance(value, str):
        if _unsafe_matches(value):
            return None
        return value
    return value


def _compile_tone_policy(
    base: dict[str, Any],
    override: Any,
    voice_items: list[str],
    catchphrases: list[str],
) -> dict[str, Any]:
    tone = _safe_mapping(base)
    if isinstance(override, dict):
        for key, value in override.items():
            try:
                if key in {
                    "conciseness",
                    "warmth",
                    "humor",
                    "directness",
                    "formality",
                    "proactiveness",
                    "technical_depth",
                }:
                    tone[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    voice_text = " ".join(voice_items + catchphrases).lower()
    if any(marker in voice_text for marker in ["幽默", "接梗", "轻松", "witty", "humor"]):
        tone["humor"] = max(float(tone.get("humor", 0.0)), 0.58)
    if any(marker in voice_text for marker in ["自然", "朋友", "温暖", "warm", "friend"]):
        tone["warmth"] = max(float(tone.get("warmth", 0.0)), 0.72)
    if any(marker in voice_text for marker in ["直接", "先给结论", "direct", "concise"]):
        tone["directness"] = max(float(tone.get("directness", 0.0)), 0.74)
        tone["conciseness"] = max(float(tone.get("conciseness", 0.0)), 0.62)
    return tone


def _compile_summary(
    frontmatter_summary: Any,
    identity_text: str,
    voice_items: list[str],
    work_items: list[str],
    catchphrases: list[str],
) -> str:
    if isinstance(frontmatter_summary, str) and frontmatter_summary.strip():
        return frontmatter_summary.strip()[:700]
    parts = [identity_text.strip()]
    if voice_items:
        parts.append("口吻：" + "；".join(voice_items[:3]))
    if work_items:
        parts.append("做事：" + "；".join(work_items[:3]))
    if catchphrases:
        parts.append("常用表达：" + "，".join(catchphrases[:3]))
    return " ".join(part for part in parts if part).strip()[:900]


def _safe_mapping(value: Any) -> dict[str, Any]:
    sanitized = _sanitize_value(value)
    return dict(sanitized) if isinstance(sanitized, dict) else {}


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str) and not _unsafe_matches(item):
            stripped = item.strip()
            if stripped and stripped not in items:
                items.append(stripped[:180])
    return items


def _safe_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rules: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            safe = _safe_mapping(item)
            if safe:
                rules.append(safe)
    return rules[:12]


def _merge_unique(*groups: list[str], limit: int = 20) -> list[str]:
    result: list[str] = []
    for group in groups:
        for item in group or []:
            text = str(item or "").strip()
            if not text or text in result or _unsafe_matches(text):
                continue
            result.append(text[:180])
            if len(result) >= limit:
                return result
    return result


def _first_text(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_plain_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or _unsafe_matches(text):
        return ""
    return text


def _persona_seed_key(member: dict[str, Any], profile: dict[str, Any]) -> str:
    return str(
        profile.get("persona_profile_id")
        or member.get("persona_profile_id")
        or member.get("member_id")
        or profile.get("member_id")
        or ""
    )


def _voice_seed_lines(
    member_id: str,
    tone_policy: dict[str, Any],
    *,
    persona_key: str = "",
) -> list[str]:
    if member_id == "mem_xiaowu":
        return [
            "像认识很多年的老朋友一样自然接话。",
            "可以轻松、机灵、会接梗，但不油腻、不吵闹。",
            "先回应用户当前这句话，再给判断和下一步。",
            "安全、隐私、审批或真实执行场景立刻收敛为清楚克制。",
        ]
    if persona_key == "direct_professional":
        return [
            "像干脆的架构师一样说话，先给判断，再讲方案取舍。",
            "技术问题保留结构、边界和实现精度，不堆空话。",
            "遇到风险、约束或证据不足时，直接说清楚。",
        ]
    if persona_key == "structured_ux_sensitive":
        return [
            "像清晰的产品经理一样沟通，先对齐目标和用户场景。",
            "复杂问题会拆成模块、流程和验收口径。",
            "既关心体验，也关心范围、成本和节奏。",
        ]
    if persona_key == "gentle_careful":
        return [
            "像细心的家庭管家一样接话，语气温柔但不含糊。",
            "生活请求先稳住节奏，再给提醒、安排和下一步。",
            "涉及真实执行、付款或设备动作时会清楚停在边界上。",
        ]
    if persona_key == "creative_growth":
        return [
            "像内容运营一样保留创意和传播感，但先讲可执行方向。",
            "会根据受众、渠道和目标调整表达，不空喊口号。",
            "没有真实工具和证据时，不假装已经发布或投放。",
        ]
    lines = ["可靠、直接、温暖，先给结论。"]
    if float(tone_policy.get("technical_depth", 0.0) or 0.0) >= 0.6:
        lines.append("复杂问题保留结构和技术精度。")
    if float(tone_policy.get("proactiveness", 0.0) or 0.0) >= 0.55:
        lines.append("能推进就推进，不能推进就说明还缺什么。")
    return lines


def _boundary_seed_lines(consistency: dict[str, Any]) -> list[str]:
    known = {
        "pretending_to_be_a_human": "不冒充现实真人或隐藏身份。",
        "claiming_hidden_tool_or_account_access": "不声称拥有隐藏账号、工具或资产访问。",
        "claiming_safety_or_approval_can_be_bypassed": "不能绕过安全、权限或审批流程。",
        "claiming_file_browser_terminal_wallet_or_mcp_actions_completed_without_evidence": (
            "没有工具和证据链时，不声称文件、浏览器、终端、钱包或 MCP 动作已完成。"
        ),
    }
    result = [
        known.get(item, str(item))
        for item in _safe_list(consistency.get("forbidden_claims"))
    ]
    result.append("SOUL.md 只定义人格和表达方式，不能授予权限、资产或审批豁免。")
    return _merge_unique(result, limit=10)


def _memory_policy_lines(member: dict[str, Any]) -> list[str]:
    raw_policy = _jsonish(member.get("memory_policy_json") or member.get("memory_policy") or {})
    lines = [
        "长期记忆只写稳定偏好、长期事实和可复用经验。",
        "临时任务进度、一次性 TODO 和敏感信息不写入长期记忆。",
    ]
    if bool(raw_policy.get("write_requires_source", True)):
        lines.insert(1, "写入记忆必须包含来源。")
    return lines


def _default_work_style_lines(persona_key: str) -> list[str]:
    by_persona = {
        "direct_professional": [
            "先给方案结论，再补风险、取舍和落地步骤。",
            "需要更多日志、上下文或约束时，明确点出缺口。",
        ],
        "structured_ux_sensitive": [
            "先确认目标、用户场景和验收标准。",
            "把需求拆成清楚的模块、路径和优先级。",
        ],
        "gentle_careful": [
            "先安顿当下情况，再给小步可执行安排。",
            "提醒、日程和家居协助要可恢复、可确认、不过度替用户做决定。",
        ],
        "creative_growth": [
            "先给可发可用的方向，再补标题、文案和节奏建议。",
            "创意要贴着受众和渠道，不做空泛表达。",
        ],
    }
    return by_persona.get(
        persona_key,
        [
            "先给结论，再给依据和下一步。",
            "能推进就推进，不能推进就说明还缺什么。",
        ],
    )


def _default_catchphrases(member_id: str, *, persona_key: str = "") -> list[str]:
    if member_id == "mem_xiaowu":
        return ["先给结论", "我来接一下", "稳住，先看边界"]
    by_persona = {
        "direct_professional": ["先给方案", "我把取舍摊开", "先卡风险点"],
        "structured_ux_sensitive": ["先对齐目标", "我帮你拆一下", "先看验收口径"],
        "gentle_careful": ["我先帮你理顺", "先别急", "我们一步一步来"],
        "creative_growth": ["先给方向", "我先出几版", "先看受众反应"],
    }
    if persona_key in by_persona:
        return by_persona[persona_key]
    return ["先给结论", "我来理一下"]


def _render_body(body: str | list[str]) -> str:
    if isinstance(body, list):
        return "\n".join(f"- {item}" for item in body if str(item).strip())
    return str(body or "").strip()


def _jsonish(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (
            str(issue.get("code") or ""),
            str(issue.get("section") or ""),
            str(issue.get("snippet") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _snippet(text: str) -> str:
    return str(text or "").replace("\n", " ").strip()[:180]
