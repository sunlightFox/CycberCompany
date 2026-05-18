from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from core_types import (
    CanonicalAssetRequirement,
    CanonicalCompatibilityReport,
    CanonicalExecutionBinding,
    CanonicalExtensionPackage,
    CanonicalMcpRequirement,
    CanonicalPermissionEnvelope,
    CanonicalRuntimeContribution,
    CanonicalSkill,
    CanonicalSkillInstruction,
    CanonicalToolRequirement,
    PermissionPreview,
)

from app.core.errors import AppError
from app.core.time import new_id

CANONICAL_VERSION = "canonical.skill.v1"
OPENCLAW_PLUGIN_FILE = "openclaw.plugin.json"
PACKAGE_JSON_FILE = "package.json"
HERMES_PLUGIN_FILE = "plugin.yaml"


@dataclass(frozen=True)
class ImportedExtension:
    package: CanonicalExtensionPackage
    compatibility: CanonicalCompatibilityReport
    permission_preview: PermissionPreview
    manifest_hash: str
    synthesized_manifest: dict[str, Any]
    source_descriptor: dict[str, Any]
    mcp_servers: list[dict[str, Any]]


class ExtensionImporter:
    source_format: str = "unknown"

    def detect(self, root: Path) -> bool:
        raise NotImplementedError

    def import_package(
        self,
        root: Path,
        *,
        source_type: str,
        source_uri: str,
    ) -> ImportedExtension:
        raise NotImplementedError


class OpenClawImporter(ExtensionImporter):
    source_format = "openclaw"

    def detect(self, root: Path) -> bool:
        return (root / OPENCLAW_PLUGIN_FILE).exists() or (
            (root / "SKILL.md").exists() and not (root / "bundle.yaml").exists()
        )

    def import_package(
        self,
        root: Path,
        *,
        source_type: str,
        source_uri: str,
    ) -> ImportedExtension:
        return _import_extension_from_root_legacy(root, source_type=source_type, source_uri=source_uri)


class HermesImporter(ExtensionImporter):
    source_format = "hermes_plugin_v1"

    def detect(self, root: Path) -> bool:
        return (root / HERMES_PLUGIN_FILE).exists()

    def import_package(
        self,
        root: Path,
        *,
        source_type: str,
        source_uri: str,
    ) -> ImportedExtension:
        return _import_hermes_extension(root, source_type=source_type, source_uri=source_uri)


class CycberBundleImporter(ExtensionImporter):
    source_format = "cycber_bundle_v1"

    def detect(self, root: Path) -> bool:
        return (root / "bundle.yaml").exists()

    def import_package(
        self,
        root: Path,
        *,
        source_type: str,
        source_uri: str,
    ) -> ImportedExtension:
        return _import_extension_from_root_legacy(root, source_type=source_type, source_uri=source_uri)


class CycberNativeImporter(CycberBundleImporter):
    source_format = "cycber_native_v1"


IMPORTERS: tuple[ExtensionImporter, ...] = (
    HermesImporter(),
    OpenClawImporter(),
    CycberBundleImporter(),
    CycberNativeImporter(),
)


def import_extension_from_root(
    root: Path,
    *,
    source_type: str,
    source_uri: str,
) -> ImportedExtension:
    for importer in IMPORTERS:
        if importer.detect(root):
            return importer.import_package(root, source_type=source_type, source_uri=source_uri)
    raise AppError(
        "PLUGIN_VALIDATE_FAILED",
        "Extension source does not contain a supported manifest",
        status_code=422,
    )


def _import_extension_from_root_legacy(
    root: Path,
    *,
    source_type: str,
    source_uri: str,
) -> ImportedExtension:
    plugin_manifest_path = root / OPENCLAW_PLUGIN_FILE
    legacy_manifest_path = root / "bundle.yaml"
    skill_path = root / "SKILL.md"

    if plugin_manifest_path.exists():
        package, raw_parts, mcp_servers = _import_openclaw_plugin(
            root,
            plugin_manifest_path,
            legacy_manifest_path if legacy_manifest_path.exists() else None,
        )
    elif legacy_manifest_path.exists():
        package, raw_parts, mcp_servers = _import_legacy_bundle(root, legacy_manifest_path)
    elif skill_path.exists():
        package, raw_parts, mcp_servers = _import_openclaw_skill(root, skill_path)
    else:
        raise AppError(
            "PLUGIN_VALIDATE_FAILED",
            "Extension source does not contain a supported manifest",
            status_code=422,
        )

    package = _enrich_package_runtime_contracts(package, root=root, mcp_servers=mcp_servers)
    smoke_check = _static_smoke_check(root, package=package, mcp_servers=mcp_servers)
    package_compatibility = _package_json_compatibility(root)
    blocked_reasons = list(smoke_check.get("blocked_reasons", []))
    compatibility_status = "blocked" if blocked_reasons else package.compatibility_status
    compatibility_notes = [
        *package.compatibility_notes,
        *package_compatibility.get("warnings", []),
        *smoke_check.get("warnings", []),
    ]
    if compatibility_status != package.compatibility_status or compatibility_notes:
        package = package.model_copy(
            update={
                "compatibility_status": compatibility_status,
                "compatibility_notes": compatibility_notes,
                "runtime_compatibility": "blocked"
                if blocked_reasons
                else package.runtime_compatibility,
            }
        )
    package = package.model_copy(
        update={
            "source_type": source_type,
            "source_uri": source_uri,
            "canonical_snapshot": package.model_dump(mode="json"),
        }
    )
    compatibility = CanonicalCompatibilityReport(
        extension_id=package.extension_id,
        source_format=package.source_format,
        canonical_version=package.canonical_version,
        compatibility_status=package.compatibility_status,
        compatibility_notes=list(package.compatibility_notes),
        missing_items=_missing_items(package),
        warnings=_warnings(package),
        compatibility_tier=package.runtime_compatibility,
        smoke_check=smoke_check,
        package_compatibility=package_compatibility,
        blocked_reasons=blocked_reasons,
    )
    synthesized_manifest = synthesize_manifest(package)
    manifest_hash = _hash_text("\n".join(raw_parts))
    preview = permission_preview_from_package(package, synthesized_manifest=synthesized_manifest)
    source_descriptor = {
        "root": str(root),
        "source_type": source_type,
        "source_uri": source_uri,
        "detected_format": package.source_format,
        "manifest_files": _manifest_files(root),
    }
    return ImportedExtension(
        package=package,
        compatibility=compatibility,
        permission_preview=preview,
        manifest_hash=manifest_hash,
        synthesized_manifest=synthesized_manifest,
        source_descriptor=source_descriptor,
        mcp_servers=mcp_servers,
    )


def bind_canonical_package(
    package: CanonicalExtensionPackage,
    *,
    builtin_tool_names: set[str],
    active_mcp_tools: list[dict[str, Any]],
) -> CanonicalExtensionPackage:
    bound_skills: list[CanonicalSkill] = []
    package_statuses: list[str] = []
    package_notes = list(package.compatibility_notes)
    active_mcp_index = [_mcp_tool_index(tool) for tool in active_mcp_tools]

    for skill in package.skills:
        builtin_hits: list[str] = []
        mcp_hits: list[str] = []
        missing: list[str] = []

        for req in skill.required_tools:
            if req.tool_name in builtin_tool_names:
                builtin_hits.append(req.tool_name)
            elif req.required:
                missing.append(f"builtin:{req.tool_name}")

        for req in package.mcp_requirements:
            if not _skill_references_mcp(skill, req):
                continue
            match = _match_mcp_requirement(req, active_mcp_index)
            if match is not None:
                mcp_hits.append(match)
            elif req.required:
                missing.append(
                    f"mcp:{req.tool_name or req.server_id or req.capability or 'unknown'}"
                )

        if skill.runtime_kind == "instruction_only" and not builtin_hits and not mcp_hits:
            binding_status = "not_required" if not missing else "degraded"
        else:
            binding_status = "ready" if not missing else "degraded"
        if missing and skill.runtime_kind != "instruction_only":
            package_notes.append(f"{skill.name} missing bindings: {', '.join(missing)}")
        original_summary = dict(skill.execution_binding.summary or {})
        execution_binding = CanonicalExecutionBinding(
            runtime_kind=skill.runtime_kind,
            status=binding_status,
            builtin_tools=builtin_hits,
            mcp_tools=mcp_hits,
            missing_requirements=missing,
            summary={
                **original_summary,
                "builtin_bound": len(builtin_hits),
                "mcp_bound": len(mcp_hits),
                "missing_count": len(missing),
                "missing_tools": [item for item in missing if item.startswith("builtin:")],
                "missing_mcp": [item for item in missing if item.startswith("mcp:")],
                "missing_env": [],
                "missing_secret": [],
                "missing_config": [],
                "next_actions": _next_actions_for_missing(missing),
            },
        )
        bound_skills.append(
            skill.model_copy(
                update={
                    "execution_binding": execution_binding,
                    "compatibility_status": "partial"
                    if binding_status == "degraded"
                    else skill.compatibility_status,
                }
            )
        )
        package_statuses.append(binding_status)

    if not package_statuses:
        compatibility_status = package.compatibility_status
    elif all(status == "ready" for status in package_statuses):
        compatibility_status = "native" if package.source_format.startswith("openclaw") else "compatible"
    elif any(status == "ready" for status in package_statuses):
        compatibility_status = "partial"
    else:
        compatibility_status = package.compatibility_status

    return package.model_copy(
        update={
            "skills": bound_skills,
            "compatibility_status": compatibility_status,
            "compatibility_notes": package_notes,
        }
    )


def synthesize_manifest(package: CanonicalExtensionPackage) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "id": package.bundle_id,
        "display_name": package.display_name,
        "description": package.description,
        "version": package.version or "1.0.0",
        "required_tools": sorted(
            {
                req.tool_name
                for skill in package.skills
                for req in skill.required_tools
            }
        ),
        "required_assets": [
            asset.model_dump(mode="json")
            for skill in package.skills
            for asset in skill.required_assets
        ],
        "permissions": package.permission_envelope.model_dump(mode="json"),
        "skills": [],
        "steps": [],
        "mcp_requirements": [req.model_dump(mode="json") for req in package.mcp_requirements],
        "source_format": package.source_format,
        "runtime_compatibility": package.runtime_compatibility,
        "config_requirements": package.config_requirements,
        "secret_requirements": package.secret_requirements,
        "env_requirements": package.env_requirements,
        "dependency_requirements": package.dependency_requirements,
        "runtime_contributions": [
            item.model_dump(mode="json") for item in package.runtime_contributions
        ],
        "setup_hints": package.setup_hints,
    }
    for skill in package.skills:
        manifest["skills"].append(
            {
                "name": skill.name,
                "display_name": skill.display_name,
                "description": skill.description,
                "entrypoint_path": skill.entrypoint_path,
                "triggers": skill.instruction_spec.trigger,
                "input_schema": skill.instruction_spec.input_schema,
                "output_schema": skill.instruction_spec.output_schema,
                "required_tools": [req.tool_name for req in skill.required_tools],
                "required_assets": [asset.model_dump(mode="json") for asset in skill.required_assets],
                "permissions": skill.permission_envelope.model_dump(mode="json"),
                "steps": skill.execution_binding.summary.get("steps")
                or skill.execution_binding.summary.get("legacy_steps")
                or [],
                "runtime_kind": skill.runtime_kind,
                "compatibility_status": skill.compatibility_status,
            }
        )
        if not manifest["steps"] and skill.execution_binding.summary.get("legacy_steps"):
            manifest["steps"] = list(skill.execution_binding.summary["legacy_steps"])
    return manifest


def permission_preview_from_package(
    package: CanonicalExtensionPackage,
    *,
    synthesized_manifest: dict[str, Any],
) -> PermissionPreview:
    required_tools = []
    tool_names = sorted(
        {
            req.tool_name
            for skill in package.skills
            for req in skill.required_tools
        }
    )
    for tool_name in tool_names:
        required_tools.append({"tool_name": tool_name, "risk_level": "R2"})
    return PermissionPreview(
        bundle_id=package.bundle_id,
        summary=(
            f"{package.display_name} requires {len(tool_names)} tools, "
            f"{len(package.mcp_requirements)} MCP requirements"
        ),
        required_tools=required_tools,
        required_assets=[
            asset.model_dump(mode="json")
            for skill in package.skills
            for asset in skill.required_assets
        ],
        network=package.permission_envelope.network,
        filesystem=package.permission_envelope.filesystem,
        high_risk_actions=[],
        blocked_actions=[],
        trust={
            "signature_status": "unsigned",
            "trust_level": package.trust_level,
            "compatibility_status": package.compatibility_status,
        },
        preview_hash=_hash_text(json.dumps(synthesized_manifest, ensure_ascii=False, sort_keys=True)),
    )


def skill_rows_from_package(package: CanonicalExtensionPackage) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for skill in package.skills:
        rows.append(
            {
                "skill_id": skill.skill_id,
                "extension_id": package.extension_id,
                "name": skill.name,
                "display_name": skill.display_name,
                "description": skill.description,
                "entrypoint_path": skill.entrypoint_path,
                "instructions": skill.instruction_spec.markdown,
                "runtime_kind": skill.runtime_kind,
                "source_format": package.source_format,
                "canonical_version": package.canonical_version,
                "compatibility_status": skill.compatibility_status,
                "compatibility_notes": skill.compatibility_notes,
                "binding_status": skill.execution_binding.status,
                "binding_summary": skill.execution_binding.summary,
                "instruction_spec": skill.instruction_spec.model_dump(mode="json"),
                "execution_binding": skill.execution_binding.model_dump(mode="json"),
                "trigger": skill.instruction_spec.trigger,
                "input_schema": skill.instruction_spec.input_schema,
                "output_schema": skill.instruction_spec.output_schema,
                "required_tools": [req.tool_name for req in skill.required_tools],
                "required_assets": [
                    asset.model_dump(mode="json") for asset in skill.required_assets
                ],
                "permission": skill.permission_envelope.model_dump(mode="json"),
                "risk_policy": {"default": "R2"},
                "eval_summary": {},
                "steps": skill.execution_binding.summary.get("legacy_steps", []),
            }
        )
    return rows


def _import_hermes_extension(
    root: Path,
    *,
    source_type: str,
    source_uri: str,
) -> ImportedExtension:
    manifest_path = root / HERMES_PLUGIN_FILE
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text) or {}
    if not isinstance(manifest, dict):
        raise AppError("PLUGIN_VALIDATE_FAILED", "plugin.yaml must be an object", status_code=422)
    bundle_id = _safe_id(str(manifest.get("name") or manifest.get("id") or root.name))
    contributions = _runtime_contributions_from_manifest(
        bundle_id=bundle_id,
        manifest=manifest,
        source_format="hermes_plugin_v1",
        mcp_servers=[],
    )
    env_requirements = _requirement_items(
        manifest.get("required_env") or manifest.get("env") or [],
        kind="env",
    )
    setup_hints = []
    if manifest.get("install_hint"):
        setup_hints.append({"kind": "install_hint", "message": str(manifest["install_hint"])})
    package = CanonicalExtensionPackage(
        extension_id=f"ext.{bundle_id}",
        bundle_id=bundle_id,
        display_name=str(manifest.get("display_name") or manifest.get("label") or bundle_id),
        description=manifest.get("description"),
        package_kind="python_plugin",
        source_type=source_type,
        source_format="hermes_plugin_v1",
        source_uri=source_uri,
        manifest_format="hermes_plugin_v1",
        canonical_version=CANONICAL_VERSION,
        compatibility_status="compatible",
        compatibility_notes=[],
        trust_level="restricted",
        version=str(manifest.get("version") or "1.0.0"),
        permission_envelope=_permission_envelope_from_sources(manifest, None),
        skills=[],
        mcp_requirements=[],
        runtime_compatibility="native_python",
        env_requirements=env_requirements,
        dependency_requirements=_requirement_items(manifest.get("dependencies") or [], kind="dependency"),
        runtime_contributions=contributions,
        setup_hints=setup_hints,
        manifest=manifest,
        canonical_snapshot={},
    )
    package = _enrich_package_runtime_contracts(package, root=root, mcp_servers=[])
    smoke_check = _static_smoke_check(root, package=package, mcp_servers=[])
    blocked_reasons = list(smoke_check.get("blocked_reasons", []))
    if blocked_reasons:
        package = package.model_copy(
            update={
                "compatibility_status": "blocked",
                "runtime_compatibility": "blocked",
                "compatibility_notes": [*package.compatibility_notes, *blocked_reasons],
            }
        )
    package = package.model_copy(
        update={
            "canonical_snapshot": package.model_dump(mode="json"),
        }
    )
    compatibility = CanonicalCompatibilityReport(
        extension_id=package.extension_id,
        source_format=package.source_format,
        canonical_version=package.canonical_version,
        compatibility_status=package.compatibility_status,
        compatibility_notes=list(package.compatibility_notes),
        missing_items=_missing_items(package),
        warnings=_warnings(package),
        compatibility_tier=package.runtime_compatibility,
        smoke_check=smoke_check,
        package_compatibility={},
        blocked_reasons=blocked_reasons,
    )
    synthesized_manifest = synthesize_manifest(package)
    return ImportedExtension(
        package=package,
        compatibility=compatibility,
        permission_preview=permission_preview_from_package(
            package,
            synthesized_manifest=synthesized_manifest,
        ),
        manifest_hash=_hash_text(manifest_text),
        synthesized_manifest=synthesized_manifest,
        source_descriptor={
            "root": str(root),
            "source_type": source_type,
            "source_uri": source_uri,
            "detected_format": package.source_format,
            "manifest_files": _manifest_files(root),
        },
        mcp_servers=[],
    )


def _import_openclaw_skill(
    root: Path,
    skill_path: Path,
) -> tuple[CanonicalExtensionPackage, list[str], list[dict[str, Any]]]:
    frontmatter, body, raw_text = _parse_skill_markdown(skill_path)
    bundle_id = _safe_id(str(frontmatter.get("name") or root.name))
    display_name = str(frontmatter.get("title") or frontmatter.get("name") or bundle_id)
    skill = _canonical_skill_from_frontmatter(
        skill_id=f"skill.{bundle_id}.{bundle_id}",
        entrypoint_path=skill_path.relative_to(root).as_posix(),
        frontmatter=frontmatter,
        markdown=body,
        fallback_name=bundle_id,
    )
    package = CanonicalExtensionPackage(
        extension_id=f"ext.{bundle_id}",
        bundle_id=bundle_id,
        display_name=display_name,
        description=frontmatter.get("description"),
        package_kind="skill_only",
        source_type="local_directory",
        source_format="openclaw_skill_v1",
        manifest_format="openclaw_skill_v1",
        canonical_version=CANONICAL_VERSION,
        compatibility_status="native",
        compatibility_notes=[],
        trust_level="restricted",
        version=str(frontmatter.get("version") or "1.0.0"),
        permission_envelope=_permission_envelope_from_sources(frontmatter, None),
        skills=[skill],
        mcp_requirements=_normalize_mcp_requirements(frontmatter),
        manifest=frontmatter,
        canonical_snapshot={},
    )
    return package, [raw_text], _normalize_mcp_servers(frontmatter)


def _import_openclaw_plugin(
    root: Path,
    plugin_manifest_path: Path,
    legacy_manifest_path: Path | None,
) -> tuple[CanonicalExtensionPackage, list[str], list[dict[str, Any]]]:
    plugin_manifest = json.loads(plugin_manifest_path.read_text(encoding="utf-8"))
    bundle_id = _safe_id(
        str(
            plugin_manifest.get("name")
            or plugin_manifest.get("id")
            or plugin_manifest.get("displayName")
            or root.name
        )
    )
    if legacy_manifest_path is not None:
        legacy_manifest = yaml.safe_load(legacy_manifest_path.read_text(encoding="utf-8")) or {}
        legacy_id = _safe_id(str(legacy_manifest.get("id") or bundle_id))
        if legacy_id != bundle_id:
            raise AppError(
                "PLUGIN_VALIDATE_FAILED",
                "Plugin manifest conflicts with legacy bundle id",
                status_code=422,
                details={"plugin_bundle_id": bundle_id, "legacy_bundle_id": legacy_id},
            )
    skill_docs: list[str] = []
    compatibility_notes: list[str] = []
    skills: list[CanonicalSkill] = []
    for skill_root in _skill_roots(root, plugin_manifest):
        if not skill_root.exists():
            compatibility_notes.append(f"skill root missing: {skill_root.relative_to(root)}")
            continue
        for skill_path in sorted(skill_root.rglob("SKILL.md")):
            try:
                frontmatter, body, raw_text = _parse_skill_markdown(skill_path)
            except AppError:
                compatibility_notes.append(f"failed to parse {skill_path.relative_to(root).as_posix()}")
                continue
            skill_name = _safe_id(
                str(frontmatter.get("name") or skill_path.parent.name or skill_path.stem)
            )
            skills.append(
                _canonical_skill_from_frontmatter(
                    skill_id=f"skill.{bundle_id}.{skill_name}",
                    entrypoint_path=skill_path.relative_to(root).as_posix(),
                    frontmatter=frontmatter,
                    markdown=body,
                    fallback_name=skill_name,
                    package_manifest=plugin_manifest,
                )
            )
            skill_docs.append(raw_text)
    if not skills:
        compatibility_status = "blocked"
        compatibility_notes.append("no valid skills discovered from plugin manifest")
    else:
        compatibility_status = "partial" if compatibility_notes else "native"
    package = CanonicalExtensionPackage(
        extension_id=f"ext.{bundle_id}",
        bundle_id=bundle_id,
        display_name=str(
            plugin_manifest.get("displayName")
            or plugin_manifest.get("name")
            or plugin_manifest.get("id")
            or bundle_id
        ),
        description=plugin_manifest.get("description"),
        package_kind="plugin_bundle",
        source_type="local_directory",
        source_format="openclaw_plugin_v1",
        source_uri=None,
        manifest_format="openclaw_plugin_v1",
        canonical_version=CANONICAL_VERSION,
        compatibility_status=compatibility_status,
        compatibility_notes=compatibility_notes,
        trust_level="restricted",
        version=str(plugin_manifest.get("version") or "1.0.0"),
        permission_envelope=_permission_envelope_from_sources(plugin_manifest, None),
        skills=skills,
        mcp_requirements=_normalize_mcp_requirements(plugin_manifest),
        manifest=plugin_manifest,
        canonical_snapshot={},
    )
    raw_parts = [plugin_manifest_path.read_text(encoding="utf-8"), *skill_docs]
    return package, raw_parts, _normalize_mcp_servers(plugin_manifest)


def _import_legacy_bundle(
    root: Path,
    manifest_path: Path,
) -> tuple[CanonicalExtensionPackage, list[str], list[dict[str, Any]]]:
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(manifest_text) or {}
    if not isinstance(manifest, dict) or not manifest.get("id"):
        raise AppError("PLUGIN_VALIDATE_FAILED", "bundle.yaml must define id", status_code=422)
    _validate_legacy_manifest_tool_contract(manifest)
    skill_path = root / "SKILL.md"
    skill_md = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    bundle_id = _safe_id(str(manifest["id"]))
    entries = manifest.get("skills") if isinstance(manifest.get("skills"), list) else None
    if not entries:
        entries = [{"name": name} for name in manifest.get("entry_skills", [bundle_id])]
    skills: list[CanonicalSkill] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = _safe_id(str(entry.get("name") or entry.get("id") or bundle_id))
        steps = entry.get("steps") or manifest.get("steps") or []
        runtime_kind = "workflow_bound" if steps else "instruction_only"
        execution_binding = CanonicalExecutionBinding(
            runtime_kind=runtime_kind,
            status="unbound" if steps else "not_required",
            summary={"legacy_steps": steps},
        )
        skill = CanonicalSkill(
            skill_id=f"skill.{bundle_id}.{name}",
            name=name,
            display_name=str(entry.get("display_name") or manifest.get("display_name") or name),
            description=entry.get("description") or manifest.get("description"),
            entrypoint_path=str(entry.get("entrypoint_path") or "SKILL.md"),
            runtime_kind=runtime_kind,
            instruction_spec=CanonicalSkillInstruction(
                markdown=skill_md,
                frontmatter={},
                trigger=entry.get("triggers") or manifest.get("triggers", {}),
                input_schema=entry.get("input_schema") or manifest.get("input_schema", {}),
                output_schema=entry.get("output_schema") or manifest.get("output_schema", {}),
                limitations=[],
            ),
            execution_binding=execution_binding,
            required_tools=[
                CanonicalToolRequirement(tool_name=tool_name)
                for tool_name in _manifest_tool_names(manifest, entry)
            ],
            required_assets=[
                CanonicalAssetRequirement(
                    asset_type=str(item.get("asset_type") or item.get("type") or "asset"),
                    optional=bool(item.get("optional", False)),
                    metadata=dict(item),
                )
                for item in _normalize_asset_items(
                    entry.get("required_assets") or manifest.get("required_assets") or []
                )
            ],
            permission_envelope=_permission_envelope_from_sources(entry, manifest),
            compatibility_status="compatible",
            compatibility_notes=[],
        )
        skills.append(skill)
    package = CanonicalExtensionPackage(
        extension_id=f"ext.{bundle_id}",
        bundle_id=bundle_id,
        display_name=str(manifest.get("display_name") or bundle_id),
        description=manifest.get("description"),
        package_kind="legacy_bundle",
        source_type="local_directory",
        source_format="cycber_bundle_v1",
        manifest_format="cycber_bundle_v1",
        canonical_version=CANONICAL_VERSION,
        compatibility_status="compatible",
        compatibility_notes=[],
        trust_level="restricted",
        version=str(manifest.get("bundle_revision") or manifest.get("version") or "1.0.0"),
        permission_envelope=_permission_envelope_from_sources(manifest, None),
        skills=skills,
        mcp_requirements=_normalize_mcp_requirements(manifest),
        manifest=manifest,
        canonical_snapshot={},
    )
    raw_parts = [manifest_text]
    if skill_md:
        raw_parts.append(skill_md)
    return package, raw_parts, _normalize_mcp_servers(manifest)


def _canonical_skill_from_frontmatter(
    *,
    skill_id: str,
    entrypoint_path: str,
    frontmatter: dict[str, Any],
    markdown: str,
    fallback_name: str,
    package_manifest: dict[str, Any] | None = None,
) -> CanonicalSkill:
    tool_names = _normalize_tool_names(frontmatter, package_manifest)
    steps = _normalize_steps(frontmatter) or _normalize_steps(package_manifest or {})
    has_runtime_binding = bool(steps or tool_names or _normalize_mcp_requirements(frontmatter))
    runtime_kind = "hybrid_bound" if steps and markdown else "instruction_only"
    if steps:
        runtime_kind = "hybrid_bound" if markdown else "workflow_bound"
    execution_binding = CanonicalExecutionBinding(
        runtime_kind=runtime_kind,
        status="unbound" if has_runtime_binding else "not_required",
        summary={"legacy_steps": steps},
    )
    return CanonicalSkill(
        skill_id=skill_id,
        name=_safe_id(str(frontmatter.get("name") or fallback_name)),
        display_name=str(frontmatter.get("title") or frontmatter.get("name") or fallback_name),
        description=frontmatter.get("description"),
        entrypoint_path=entrypoint_path,
        runtime_kind=runtime_kind,
        instruction_spec=CanonicalSkillInstruction(
            markdown=markdown,
            frontmatter=frontmatter,
            trigger=_normalize_trigger(frontmatter),
            input_schema=_normalize_schema(frontmatter.get("input_schema") or frontmatter.get("input")),
            output_schema=_normalize_schema(frontmatter.get("output_schema") or frontmatter.get("output")),
            limitations=_normalize_limitations(frontmatter),
        ),
        execution_binding=execution_binding,
        required_tools=[CanonicalToolRequirement(tool_name=name) for name in tool_names],
        required_assets=[
            CanonicalAssetRequirement(
                asset_type=str(item.get("asset_type") or item.get("type") or "asset"),
                optional=bool(item.get("optional", False)),
                metadata=dict(item),
            )
            for item in _normalize_asset_items(frontmatter.get("required_assets") or [])
        ],
        permission_envelope=_permission_envelope_from_sources(frontmatter, package_manifest),
        compatibility_status="native",
        compatibility_notes=[],
    )


def _parse_skill_markdown(skill_path: Path) -> tuple[dict[str, Any], str, str]:
    raw_text = skill_path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw_text, flags=re.DOTALL)
    if not match:
        raise AppError(
            "PLUGIN_VALIDATE_FAILED",
            f"{skill_path.name} is missing YAML frontmatter",
            status_code=422,
        )
    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict):
        raise AppError("PLUGIN_VALIDATE_FAILED", "SKILL frontmatter must be an object", status_code=422)
    body = match.group(2).strip()
    return frontmatter, body, raw_text


def _skill_roots(root: Path, manifest: dict[str, Any]) -> list[Path]:
    raw = manifest.get("skills")
    if not isinstance(raw, list) or not raw:
        return [root / "skills", root]
    roots: list[Path] = []
    for item in raw:
        if isinstance(item, str):
            roots.append((root / item).resolve())
        elif isinstance(item, dict) and item.get("root"):
            roots.append((root / str(item["root"])).resolve())
    return roots or [root]


def _permission_envelope_from_sources(
    primary: dict[str, Any],
    fallback: dict[str, Any] | None,
) -> CanonicalPermissionEnvelope:
    permissions = primary.get("permissions")
    if not isinstance(permissions, dict):
        permissions = fallback.get("permissions", {}) if isinstance(fallback, dict) else {}
    network = primary.get("network")
    if not isinstance(network, dict):
        network = permissions.get("network") or permissions.get("net") or {}
    filesystem = primary.get("filesystem")
    if not isinstance(filesystem, dict):
        filesystem = permissions.get("filesystem") or permissions.get("fs") or {}
    environment = primary.get("environment")
    if not isinstance(environment, dict):
        environment = permissions.get("environment") or {}
    return CanonicalPermissionEnvelope(
        tools=_normalize_permission_tools(permissions.get("tools") or primary.get("required_tools") or []),
        mcp=_normalize_permission_mcp(permissions.get("mcp") or primary.get("mcp") or []),
        assets=_normalize_asset_items(permissions.get("assets") or primary.get("required_assets") or []),
        network=network if isinstance(network, dict) else {},
        filesystem=filesystem if isinstance(filesystem, dict) else {},
        environment=environment if isinstance(environment, dict) else {},
    )


def _normalize_trigger(frontmatter: dict[str, Any]) -> dict[str, Any]:
    trigger = frontmatter.get("trigger") or frontmatter.get("triggers") or {}
    if isinstance(trigger, dict):
        return trigger
    if isinstance(trigger, list):
        return {"keywords": [str(item) for item in trigger]}
    return {}


def _normalize_schema(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _normalize_limitations(frontmatter: dict[str, Any]) -> list[str]:
    raw = frontmatter.get("limitations") or frontmatter.get("constraints") or []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _normalize_tool_names(primary: dict[str, Any], fallback: dict[str, Any] | None = None) -> list[str]:
    names: list[str] = []
    raw = primary.get("tools") or primary.get("required_tools") or []
    if not raw and isinstance(fallback, dict):
        raw = fallback.get("required_tools") or []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return sorted(set(names))


def _manifest_tool_names(manifest: dict[str, Any], entry: dict[str, Any] | None = None) -> list[str]:
    names = set(_normalize_tool_names(manifest))
    if isinstance(entry, dict):
        names.update(_normalize_tool_names(entry))
    return sorted(names)


def _normalize_steps(raw: dict[str, Any]) -> list[dict[str, Any]]:
    steps = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        return []
    return [dict(step) for step in steps if isinstance(step, dict)]


def _validate_legacy_manifest_tool_contract(manifest: dict[str, Any]) -> None:
    declared_root_tools = set(_manifest_tool_names(manifest, None))
    for tool_name in _step_tool_names(_normalize_steps(manifest)):
        if tool_name not in declared_root_tools:
            raise AppError(
                "PLUGIN_VALIDATE_FAILED",
                "steps used undeclared required_tools",
                status_code=422,
                details={"tool_name": tool_name, "field": "steps"},
            )
    entries = manifest.get("skills") if isinstance(manifest.get("skills"), list) else []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        declared_tools = set(_manifest_tool_names(manifest, entry))
        for tool_name in _step_tool_names(_normalize_steps(entry)):
            if tool_name not in declared_tools:
                raise AppError(
                    "PLUGIN_VALIDATE_FAILED",
                    "skill steps used undeclared required_tools",
                    status_code=422,
                    details={"tool_name": tool_name, "field": f"skills[{index}].steps"},
                )


def _step_tool_names(steps: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for step in steps:
        tool_name = str(step.get("tool_name") or step.get("tool") or "").strip()
        if tool_name:
            names.append(tool_name)
    return names


def _normalize_mcp_requirements(manifest: dict[str, Any]) -> list[CanonicalMcpRequirement]:
    raw = manifest.get("mcp_requirements") or manifest.get("mcp") or manifest.get("mcp_tools") or []
    if isinstance(raw, dict):
        raw = raw.get("requirements") or raw.get("tools") or []
    requirements: list[CanonicalMcpRequirement] = []
    for item in raw:
        if isinstance(item, str):
            requirements.append(CanonicalMcpRequirement(tool_name=item))
        elif isinstance(item, dict):
            requirements.append(
                CanonicalMcpRequirement(
                    server_id=item.get("server_id"),
                    tool_name=item.get("tool_name") or item.get("name"),
                    capability=item.get("capability"),
                    required=bool(item.get("required", True)),
                    permission=item.get("permission") if isinstance(item.get("permission"), dict) else {},
                )
            )
    for server in _normalize_mcp_servers(manifest):
        for tool in server.get("declared_tools", []):
            requirements.append(
                CanonicalMcpRequirement(
                    server_id=server["server_id"],
                    tool_name=tool,
                    required=True,
                    permission=server.get("permission", {}),
                )
            )
    deduped: dict[str, CanonicalMcpRequirement] = {}
    for item in requirements:
        key = f"{item.server_id}:{item.tool_name}:{item.capability}"
        deduped[key] = item
    return list(deduped.values())


def _normalize_mcp_servers(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("mcp_servers") or manifest.get("mcpServers") or []
    if isinstance(raw, dict):
        raw = [dict(value, server_id=key) for key, value in raw.items() if isinstance(value, dict)]
    servers: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        server_id = _safe_id(str(item.get("server_id") or item.get("name") or item.get("id") or new_id("mcp")))
        declared_tools = []
        for tool in item.get("tools") or item.get("required_tools") or []:
            if isinstance(tool, str):
                declared_tools.append(tool)
            elif isinstance(tool, dict) and tool.get("name"):
                declared_tools.append(str(tool["name"]))
        servers.append(
            {
                "server_id": server_id,
                "display_name": str(item.get("display_name") or item.get("name") or server_id),
                "description": item.get("description"),
                "transport": str(item.get("transport") or "stdio"),
                "command": item.get("command"),
                "args": item.get("args") if isinstance(item.get("args"), list) else [],
                "url": item.get("url"),
                "env_refs": item.get("env_refs") if isinstance(item.get("env_refs"), list) else [],
                "permission": item.get("permission") if isinstance(item.get("permission"), dict) else {},
                "risk_policy": item.get("risk_policy") if isinstance(item.get("risk_policy"), dict) else {},
                "trust_level": str(item.get("trust_level") or "restricted"),
                "declared_tools": declared_tools,
            }
        )
    return servers


def _normalize_permission_tools(raw: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                items.append({"name": item})
            elif isinstance(item, dict):
                items.append(dict(item))
    return items


def _normalize_permission_mcp(raw: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                items.append({"tool_name": item})
            elif isinstance(item, dict):
                items.append(dict(item))
    return items


def _normalize_asset_items(raw: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                items.append({"asset_type": item})
            elif isinstance(item, dict):
                items.append(dict(item))
    return items


def _skill_references_mcp(skill: CanonicalSkill, requirement: CanonicalMcpRequirement) -> bool:
    if requirement.tool_name and any(req.tool_name == requirement.tool_name for req in skill.required_tools):
        return True
    permissions = skill.permission_envelope.mcp
    for item in permissions:
        if requirement.tool_name and item.get("tool_name") == requirement.tool_name:
            return True
        if requirement.server_id and item.get("server_id") == requirement.server_id:
            return True
    return not permissions and bool(requirement.tool_name or requirement.server_id)


def _mcp_tool_index(tool: dict[str, Any]) -> dict[str, str]:
    return {
        "server_id": str(tool.get("server_id") or ""),
        "tool_name": str(tool.get("tool_name") or ""),
        "registry_tool_name": str(tool.get("registry_tool_name") or ""),
        "description": str(tool.get("description") or ""),
    }


def _match_mcp_requirement(
    requirement: CanonicalMcpRequirement,
    active_tools: list[dict[str, str]],
) -> str | None:
    for tool in active_tools:
        if requirement.server_id and tool["server_id"] != requirement.server_id:
            continue
        if requirement.tool_name and requirement.tool_name in {
            tool["tool_name"],
            tool["registry_tool_name"],
        }:
            return tool["registry_tool_name"] or tool["tool_name"]
        if requirement.capability and requirement.capability.lower() in (
            f"{tool['tool_name']} {tool['description']}".lower()
        ):
            return tool["registry_tool_name"] or tool["tool_name"]
    return None


def _next_actions_for_missing(missing: list[str]) -> list[dict[str, Any]]:
    actions = []
    for item in missing:
        if item.startswith("mcp:"):
            actions.append({"kind": "enable_mcp", "target": item.removeprefix("mcp:")})
        elif item.startswith("builtin:"):
            actions.append({"kind": "enable_tool", "target": item.removeprefix("builtin:")})
    return actions


def _enrich_package_runtime_contracts(
    package: CanonicalExtensionPackage,
    *,
    root: Path,
    mcp_servers: list[dict[str, Any]],
) -> CanonicalExtensionPackage:
    manifest = dict(package.manifest)
    package_compat = _package_json_compatibility(root)
    if package_compat:
        manifest["_package_json_openclaw"] = package_compat
    package_json = _read_package_json(root)
    if package_json:
        manifest["_package_json_runtime"] = {
            "main": package_json.get("main"),
            "exports": package_json.get("exports"),
            "bin": package_json.get("bin"),
            "scripts": package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {},
            "bridge": (
                package_json.get("openclaw", {}).get("bridge")
                if isinstance(package_json.get("openclaw"), dict)
                else {}
            ),
        }
    if mcp_servers:
        manifest["_cycber_mcp_servers"] = mcp_servers

    runtime_compatibility = _runtime_compatibility_for(package, root=root, mcp_servers=mcp_servers)
    config_requirements = _config_requirements_from_manifest(manifest)
    secret_requirements = _secret_requirements_from_manifest(manifest)
    env_requirements = _env_requirements_from_manifest(manifest, mcp_servers=mcp_servers)
    dependency_requirements = _dependency_requirements_from_package_json(root)
    setup_hints = _setup_hints_from_manifest(manifest, dependency_requirements)
    runtime_contributions = _runtime_contributions_from_manifest(
        bundle_id=package.bundle_id,
        manifest=manifest,
        source_format=package.source_format,
        mcp_servers=mcp_servers,
    )
    return package.model_copy(
        update={
            "manifest": manifest,
            "runtime_compatibility": runtime_compatibility,
            "config_requirements": config_requirements,
            "secret_requirements": secret_requirements,
            "env_requirements": env_requirements,
            "dependency_requirements": dependency_requirements,
            "runtime_contributions": runtime_contributions,
            "setup_hints": setup_hints,
        }
    )


def _runtime_compatibility_for(
    package: CanonicalExtensionPackage,
    *,
    root: Path,
    mcp_servers: list[dict[str, Any]],
) -> str:
    if package.source_format == "hermes_plugin_v1":
        return "native_python"
    if mcp_servers:
        return "mcp_compatible"
    package_json = _read_package_json(root)
    if package_json and (
        package_json.get("main")
        or package_json.get("exports")
        or package_json.get("bin")
    ):
        return "external_runtime"
    return "manifest_compatible"


def _package_json_compatibility(root: Path) -> dict[str, Any]:
    package_json = _read_package_json(root)
    if not package_json:
        return {}
    openclaw = package_json.get("openclaw") if isinstance(package_json.get("openclaw"), dict) else {}
    compat = openclaw.get("compat") if isinstance(openclaw.get("compat"), dict) else {}
    build = openclaw.get("build") if isinstance(openclaw.get("build"), dict) else {}
    install = openclaw.get("install") if isinstance(openclaw.get("install"), dict) else {}
    warnings = []
    if openclaw and not compat.get("pluginApi"):
        warnings.append("package.json openclaw.compat.pluginApi is missing")
    if openclaw and not build.get("openclawVersion"):
        warnings.append("package.json openclaw.build.openclawVersion is missing")
    return {
        "package_name": package_json.get("name"),
        "package_version": package_json.get("version"),
        "plugin_api_range": compat.get("pluginApi"),
        "built_with_openclaw_version": build.get("openclawVersion"),
        "plugin_sdk_version": build.get("pluginSdkVersion"),
        "min_gateway_version": compat.get("minGatewayVersion") or install.get("minHostVersion"),
        "main": package_json.get("main"),
        "exports": package_json.get("exports"),
        "warnings": warnings,
    }


def _static_smoke_check(
    root: Path,
    *,
    package: CanonicalExtensionPackage,
    mcp_servers: list[dict[str, Any]],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocked_reasons: list[str] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            blocked_reasons.append(f"{name}: {detail}".strip(": "))

    add("root_exists", root.exists() and root.is_dir(), str(root))
    manifest_files = _manifest_files(root)
    add("manifest_present", bool(manifest_files), ", ".join(manifest_files))
    for skill in package.skills:
        skill_path = root / skill.entrypoint_path
        add(f"skill_entrypoint:{skill.entrypoint_path}", skill_path.exists(), str(skill_path))
    for skill_root in _declared_skill_roots(root, package.manifest):
        if not skill_root.exists():
            warnings.append(f"declared skill root missing: {skill_root}")
    package_json = _read_package_json(root)
    if package_json:
        main = package_json.get("main")
        if isinstance(main, str) and main.strip():
            add("package_main_entry", (root / main).exists(), main)
    for server in mcp_servers:
        transport = str(server.get("transport") or "stdio")
        has_endpoint = bool(server.get("url")) if transport in {"http", "sse"} else bool(server.get("command"))
        add(f"mcp_endpoint:{server.get('server_id')}", has_endpoint, "command or url required")
    declared_tools = _declared_contract_tools(package.manifest)
    required_tools = {
        req.tool_name
        for skill in package.skills
        for req in skill.required_tools
    }
    undeclared = sorted(required_tools - declared_tools) if declared_tools else []
    if undeclared:
        warnings.append(f"required tools missing from contracts.tools: {', '.join(undeclared)}")
    return {
        "status": "blocked" if blocked_reasons else ("warning" if warnings else "passed"),
        "checks": checks,
        "warnings": warnings,
        "blocked_reasons": blocked_reasons,
    }


def _runtime_contributions_from_manifest(
    *,
    bundle_id: str,
    manifest: dict[str, Any],
    source_format: str,
    mcp_servers: list[dict[str, Any]],
) -> list[CanonicalRuntimeContribution]:
    contributions: list[CanonicalRuntimeContribution] = []
    for server in mcp_servers:
        server_id = str(server.get("server_id") or "")
        if server_id:
            contributions.append(
                CanonicalRuntimeContribution(
                    contribution_id=f"extcontrib.{bundle_id}.mcp.{_safe_id(server_id)}",
                    contribution_type="mcp",
                    runtime_kind="mcp",
                    name=server_id,
                    details=server,
                )
            )
    for tool in sorted(_declared_contract_tools(manifest)):
        contributions.append(
            CanonicalRuntimeContribution(
                contribution_id=f"extcontrib.{bundle_id}.tool.{_safe_id(tool)}",
                contribution_type="tool",
                runtime_kind="python" if source_format == "hermes_plugin_v1" else "manifest",
                name=tool,
                details={"tool_name": tool},
            )
        )
    for channel in _string_list(manifest.get("channels")):
        contributions.append(
            CanonicalRuntimeContribution(
                contribution_id=f"extcontrib.{bundle_id}.channel.{_safe_id(channel)}",
                contribution_type="channel",
                runtime_kind="external_runtime" if source_format.startswith("openclaw") else "python",
                name=channel,
                details={"provider": channel},
            )
        )
    for provider in _string_list(manifest.get("provides_web_providers")):
        contributions.append(
            CanonicalRuntimeContribution(
                contribution_id=f"extcontrib.{bundle_id}.platform.{_safe_id(provider)}",
                contribution_type="external_platform",
                runtime_kind="python",
                name=provider,
                details={"provider_key": provider, "source": "hermes"},
            )
        )
    package_runtime = (
        manifest.get("_package_json_runtime")
        if isinstance(manifest.get("_package_json_runtime"), dict)
        else {}
    )
    bridge_config = manifest.get("bridge") or package_runtime.get("bridge") or {}
    if (
        manifest.get("main")
        or manifest.get("node_bridge")
        or manifest.get("bridge")
        or package_runtime.get("main")
        or package_runtime.get("exports")
        or package_runtime.get("bin")
    ):
        scripts = package_runtime.get("scripts") if isinstance(package_runtime.get("scripts"), dict) else {}
        start_command = (
            bridge_config.get("start_command")
            if isinstance(bridge_config, dict)
            else None
        ) or scripts.get("openclaw:start") or scripts.get("start")
        contributions.append(
            CanonicalRuntimeContribution(
                contribution_id=f"extcontrib.{bundle_id}.bridge.external",
                contribution_type="route",
                runtime_kind="external_runtime",
                name="external_runtime_bridge",
                details={
                    "bridge_modes": ["mcp", "stdio_process", "http_adapter", "webhook", "node_bridge"],
                    "configured": bridge_config,
                    "start_command": start_command,
                    "entrypoint": package_runtime.get("main") or manifest.get("main"),
                    "exports": package_runtime.get("exports"),
                    "health_check": bridge_config.get("health_check")
                    if isinstance(bridge_config, dict)
                    else None,
                    "env_refs": _string_list(bridge_config.get("env_refs"))
                    if isinstance(bridge_config, dict)
                    else [],
                    "log_capture_policy": bridge_config.get("log_capture_policy", "redacted")
                    if isinstance(bridge_config, dict)
                    else "redacted",
                    "shutdown_policy": bridge_config.get("shutdown_policy", "terminate_process")
                    if isinstance(bridge_config, dict)
                    else "terminate_process",
                },
            )
        )
    return contributions


def _config_requirements_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    schema = manifest.get("configSchema") or manifest.get("config_schema") or {}
    hints = manifest.get("configUiHints") or manifest.get("config_ui_hints") or {}
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(_string_list(schema.get("required")))
    return [
        {
            "key": key,
            "required": key in required,
            "schema": value if isinstance(value, dict) else {},
            "ui_hint": hints.get(key, {}) if isinstance(hints, dict) else {},
        }
        for key, value in properties.items()
    ]


def _secret_requirements_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    hints = manifest.get("configUiHints") or manifest.get("config_ui_hints") or {}
    if not isinstance(hints, dict):
        return []
    return [
        {"key": key, "required": True, "source": "configUiHints"}
        for key, hint in hints.items()
        if isinstance(hint, dict) and bool(hint.get("sensitive"))
    ]


def _env_requirements_from_manifest(
    manifest: dict[str, Any],
    *,
    mcp_servers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items = _requirement_items(
        manifest.get("required_env") or manifest.get("env_requirements") or [],
        kind="env",
    )
    channel_env = manifest.get("channelEnvVars")
    if isinstance(channel_env, dict):
        for channel, values in channel_env.items():
            for value in _string_list(values):
                items.append({"name": value, "required": True, "source": f"channel:{channel}"})
    for server in mcp_servers:
        for value in _string_list(server.get("env_refs")):
            items.append({"name": value, "required": True, "source": f"mcp:{server['server_id']}"})
    return _dedupe_dicts(items, "name")


def _dependency_requirements_from_package_json(root: Path) -> list[dict[str, Any]]:
    package_json = _read_package_json(root)
    if not package_json:
        return []
    deps = package_json.get("dependencies")
    if not isinstance(deps, dict):
        return []
    return [
        {"name": str(name), "version": str(version), "required": True, "source": "package.json"}
        for name, version in deps.items()
    ]


def _setup_hints_from_manifest(
    manifest: dict[str, Any],
    dependency_requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hints = []
    if dependency_requirements:
        hints.append({"kind": "install", "message": "Install declared package dependencies before enabling external runtime bridges."})
    for item in _string_list(manifest.get("setup_hints")):
        hints.append({"kind": "setup", "message": item})
    return hints


def _declared_contract_tools(manifest: dict[str, Any]) -> set[str]:
    contracts = manifest.get("contracts")
    if isinstance(contracts, dict):
        return set(_string_list(contracts.get("tools")))
    return set()


def _declared_skill_roots(root: Path, manifest: dict[str, Any]) -> list[Path]:
    try:
        return _skill_roots(root, manifest)
    except Exception:
        return []


def _read_package_json(root: Path) -> dict[str, Any]:
    path = root / PACKAGE_JSON_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _requirement_items(raw: Any, *, kind: str) -> list[dict[str, Any]]:
    items = []
    for value in _string_list(raw):
        items.append({"name": value, "required": True, "source": kind})
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("key") or "").strip()
                if name:
                    items.append({**item, "name": name, "required": bool(item.get("required", True))})
    return _dedupe_dicts(items, "name")


def _string_list(raw: Any) -> list[str]:
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, tuple):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _dedupe_dicts(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        value = str(item.get(key) or "").strip()
        if value and value not in deduped:
            deduped[value] = item
    return list(deduped.values())


def _missing_items(package: CanonicalExtensionPackage) -> list[str]:
    missing: list[str] = []
    if not package.skills:
        missing.append("skills")
    return missing


def _warnings(package: CanonicalExtensionPackage) -> list[str]:
    warnings: list[str] = []
    if package.compatibility_status == "partial":
        warnings.append("package imported with partial compatibility")
    return warnings


def _manifest_files(root: Path) -> list[str]:
    files: list[str] = []
    for name in ("bundle.yaml", "SKILL.md", OPENCLAW_PLUGIN_FILE, PACKAGE_JSON_FILE, HERMES_PLUGIN_FILE):
        if (root / name).exists():
            files.append(name)
    return files


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "extension"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
