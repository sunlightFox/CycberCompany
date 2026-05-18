from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ExtensionRuntimeContributionDraft:
    contribution_type: str
    name: str
    runtime_kind: str = "python"
    status: str = "ready"
    details: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    contribution_id: str | None = None


@dataclass
class ExtensionRuntimeActivationContext:
    extension_id: str
    organization_id: str
    bundle_id: str | None
    canonical: dict[str, Any]
    package: dict[str, Any]
    source_root: Path | None = None
    trace_id: str | None = None

    @property
    def manifest(self) -> dict[str, Any]:
        manifest = self.canonical.get("manifest")
        return manifest if isinstance(manifest, dict) else {}


@dataclass
class ExtensionRuntimeActivationResult:
    driver_id: str
    status: str
    contributions: list[ExtensionRuntimeContributionDraft] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class ExtensionRuntimeDriver(Protocol):
    driver_id: str

    def supports(self, context: ExtensionRuntimeActivationContext) -> bool: ...

    def activate(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> ExtensionRuntimeActivationResult: ...

    def deactivate(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> ExtensionRuntimeActivationResult: ...

    def health(self, context: ExtensionRuntimeActivationContext) -> dict[str, Any]: ...


class ExtensionRuntimeDriverRegistry:
    def __init__(self, drivers: list[ExtensionRuntimeDriver] | None = None) -> None:
        self._drivers = drivers or [
            PythonInProcessRuntimeDriver(),
            NodeBridgeRuntimeDriver(),
        ]

    def drivers_for(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> list[ExtensionRuntimeDriver]:
        return [driver for driver in self._drivers if driver.supports(context)]

    def activate_all(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> list[ExtensionRuntimeActivationResult]:
        return [driver.activate(context) for driver in self.drivers_for(context)]

    def deactivate_all(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> list[ExtensionRuntimeActivationResult]:
        return [driver.deactivate(context) for driver in self.drivers_for(context)]

    def health_all(self, context: ExtensionRuntimeActivationContext) -> list[dict[str, Any]]:
        return [
            {"driver_id": driver.driver_id, **driver.health(context)}
            for driver in self.drivers_for(context)
        ]


class PythonExtensionRegisterContext:
    def __init__(self) -> None:
        self._contributions: list[ExtensionRuntimeContributionDraft] = []
        self._health: dict[str, Any] = {"status": "not_declared"}

    @property
    def contributions(self) -> list[ExtensionRuntimeContributionDraft]:
        return list(self._contributions)

    @property
    def health(self) -> dict[str, Any]:
        return dict(self._health)

    def register_tool(
        self,
        name: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        risk_policy: dict[str, Any] | None = None,
    ) -> None:
        self._contributions.append(
            ExtensionRuntimeContributionDraft(
                contribution_type="tool",
                name=name,
                details={
                    "tool_name": name,
                    "display_name": display_name or name,
                    "description": description,
                    "input_schema": input_schema or {},
                    "output_schema": output_schema or {},
                    "risk_policy": risk_policy or {"risk_level": "R2"},
                },
            )
        )

    def register_toolset(
        self,
        name: str,
        *,
        tools: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._contributions.append(
            ExtensionRuntimeContributionDraft(
                contribution_type="toolset",
                name=name,
                details={"toolset": name, "tools": tools or [], "metadata": metadata or {}},
            )
        )

    def register_channel(self, provider: str, *, metadata: dict[str, Any] | None = None) -> None:
        self._contributions.append(
            ExtensionRuntimeContributionDraft(
                contribution_type="channel",
                name=provider,
                details={"provider": provider, "metadata": metadata or {}},
            )
        )

    def register_external_platform(
        self,
        provider: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._contributions.append(
            ExtensionRuntimeContributionDraft(
                contribution_type="external_platform",
                name=provider,
                details={"provider_key": provider, "metadata": metadata or {}},
            )
        )

    def register_health_check(
        self,
        name: str,
        *,
        status: str = "ready",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._health = {"name": name, "status": status, "metadata": metadata or {}}
        self._contributions.append(
            ExtensionRuntimeContributionDraft(
                contribution_type="health_check",
                name=name,
                details=self._health,
            )
        )


class PythonInProcessRuntimeDriver:
    driver_id = "python_inprocess"

    def supports(self, context: ExtensionRuntimeActivationContext) -> bool:
        return (
            context.canonical.get("runtime_compatibility") == "native_python"
            or _python_entrypoint(context.manifest) is not None
        )

    def activate(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> ExtensionRuntimeActivationResult:
        entrypoint = _python_entrypoint(context.manifest)
        if entrypoint is None:
            return ExtensionRuntimeActivationResult(
                driver_id=self.driver_id,
                status="not_required",
                health={"status": "not_declared"},
            )
        if context.source_root is None:
            return self._error_result("blocked", ["Extension source root is unavailable"])
        path = (context.source_root / entrypoint).resolve()
        try:
            path.relative_to(context.source_root.resolve())
        except ValueError:
            return self._error_result(
                "blocked",
                [f"Python entrypoint escapes extension root: {entrypoint}"],
            )
        if not path.exists() or not path.is_file():
            return self._error_result(
                "blocked",
                [f"Python entrypoint not found: {entrypoint}"],
            )
        try:
            spec = importlib.util.spec_from_file_location(
                f"cycber_extension_{abs(hash(str(path)))}",
                path,
            )
            if spec is None or spec.loader is None:
                return self._error_result(
                    "blocked",
                    [f"Python entrypoint cannot be loaded: {entrypoint}"],
                )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                return self._error_result(
                    "blocked",
                    [f"Python entrypoint does not expose register(context): {entrypoint}"],
                )
            register_context = PythonExtensionRegisterContext()
            register(register_context)
            return ExtensionRuntimeActivationResult(
                driver_id=self.driver_id,
                status="ready",
                contributions=[
                    _with_driver_evidence(item, self.driver_id, register_context.health, [])
                    for item in register_context.contributions
                ],
                health=register_context.health,
            )
        except Exception as exc:
            return self._error_result("runtime_error", [f"{type(exc).__name__}: {exc}"])

    def deactivate(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> ExtensionRuntimeActivationResult:
        return ExtensionRuntimeActivationResult(
            driver_id=self.driver_id,
            status="disabled",
            health={"status": "disabled"},
        )

    def health(self, context: ExtensionRuntimeActivationContext) -> dict[str, Any]:
        return {"status": "not_declared" if _python_entrypoint(context.manifest) is None else "unknown"}

    def _error_result(
        self,
        status: str,
        errors: list[str],
    ) -> ExtensionRuntimeActivationResult:
        health = {"status": status, "errors": errors}
        return ExtensionRuntimeActivationResult(
            driver_id=self.driver_id,
            status=status,
            health=health,
            errors=errors,
            contributions=[
                ExtensionRuntimeContributionDraft(
                    contribution_type="health_check",
                    name="python_loader",
                    runtime_kind="python",
                    status=status,
                    details=health,
                    evidence={
                        "driver_id": self.driver_id,
                        "loader": "python_register_context",
                        "errors": errors,
                    },
                    contribution_id=None,
                )
            ],
        )


class NodeBridgeRuntimeDriver:
    driver_id = "node_bridge"

    def supports(self, context: ExtensionRuntimeActivationContext) -> bool:
        return bool(_external_bridge_contributions(context.canonical))

    def activate(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> ExtensionRuntimeActivationResult:
        contributions = [
            ExtensionRuntimeContributionDraft(
                contribution_id=item.get("contribution_id"),
                contribution_type=str(item.get("contribution_type") or "route"),
                name=str(item.get("name") or "external_runtime_bridge"),
                runtime_kind="external_runtime",
                status="external_runtime_required",
                details={
                    **(item.get("details") if isinstance(item.get("details"), dict) else {}),
                    "driver_id": self.driver_id,
                    "supervisor": "not_started",
                    "process_started": False,
                },
                evidence={
                    "driver_id": self.driver_id,
                    "activation": "diagnostic_only",
                    "process_started": False,
                },
            )
            for item in _external_bridge_contributions(context.canonical)
        ]
        return ExtensionRuntimeActivationResult(
            driver_id=self.driver_id,
            status="external_runtime_required",
            contributions=contributions,
            health={
                "status": "external_runtime_required",
                "driver_id": self.driver_id,
                "process_started": False,
            },
        )

    def deactivate(
        self,
        context: ExtensionRuntimeActivationContext,
    ) -> ExtensionRuntimeActivationResult:
        contributions = [
            ExtensionRuntimeContributionDraft(
                contribution_id=item.get("contribution_id"),
                contribution_type=str(item.get("contribution_type") or "route"),
                name=str(item.get("name") or "external_runtime_bridge"),
                runtime_kind="external_runtime",
                status="disabled",
                details=item.get("details") if isinstance(item.get("details"), dict) else {},
                evidence={"driver_id": self.driver_id, "process_started": False},
            )
            for item in _external_bridge_contributions(context.canonical)
        ]
        return ExtensionRuntimeActivationResult(
            driver_id=self.driver_id,
            status="disabled",
            contributions=contributions,
            health={"status": "disabled", "driver_id": self.driver_id},
        )

    def health(self, context: ExtensionRuntimeActivationContext) -> dict[str, Any]:
        if not self.supports(context):
            return {"status": "not_required"}
        return {
            "status": "external_runtime_required",
            "driver_id": self.driver_id,
            "process_started": False,
        }


def _with_driver_evidence(
    contribution: ExtensionRuntimeContributionDraft,
    driver_id: str,
    health: dict[str, Any],
    errors: list[str],
) -> ExtensionRuntimeContributionDraft:
    contribution.evidence = {
        **contribution.evidence,
        "driver_id": driver_id,
        "loader": "python_register_context",
        "health": health,
        "errors": errors,
    }
    return contribution


def _external_bridge_contributions(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    contributions = canonical.get("runtime_contributions")
    if not isinstance(contributions, list):
        return []
    return [
        item
        for item in contributions
        if isinstance(item, dict)
        and item.get("runtime_kind") == "external_runtime"
        and item.get("contribution_type") == "route"
    ]


def _python_entrypoint(manifest: dict[str, Any]) -> str | None:
    runtime = manifest.get("runtime")
    if isinstance(runtime, dict):
        for key in ("python_entrypoint", "entrypoint", "module_path"):
            value = runtime.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("python_entrypoint", "pythonModule", "module_path"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
