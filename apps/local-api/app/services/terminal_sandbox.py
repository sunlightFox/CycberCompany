from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core_types import ErrorCode, TerminalSandboxProfile
from trace_service import redact

from app.core.errors import AppError

SANDBOX_BACKENDS = [
    "windows_job_object",
    "container",
    "policy_guard",
]
LEGACY_SANDBOX_BACKENDS = {"windows_low_integrity", "disabled"}
SAFE_ENV_KEYS = {
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "TEMP",
    "TMP",
    "WINDIR",
}
SECRET_ENV_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|cookie|private[_-]?key|mnemonic|password)"
)
TRAVERSAL_RE = re.compile(r"(^|[\\/\s'\"`])\.\.([\\/]|$)")
SENSITIVE_PATH_RE = re.compile(
    r"(?i)"
    r"\b[A-Za-z]:\\Users\\[^\\\s]+"
    r"|\\AppData\\"
    r"|\\Windows\\"
    r"|\\ProgramData\\"
    r"|\\cycbercompany\\data\\secrets\\"
    r"|(^|[\\/\s])(?:wallet|browser profiles?|local_secrets\.json|master\.key)([\\/\s]|$)"
    r"|(^|[\\/\s])\.ssh([\\/\s]|$)"
)
SYMLINK_RE = re.compile(
    r"(?i)(\bmklink\b|\bln\s+-s\b|symlink_to|symboliclink|new-item\s+.*itemtype\s+symbolic)"
)
NETWORK_RE = re.compile(
    r"(?i)(\bcurl\b|\bwget\b|invoke-webrequest|invoke-restmethod|start-bitstransfer)"
)
NETWORK_WRITE_RE = re.compile(
    r"(?i)(--request\s+post|-x\s+post|-method\s+post|\s-d\s+|--data|upload|login|payment|pay|checkout|transfer|wallet|sign)"
)
SHELL_META_EXE_RE = re.compile(r"^\s*([^\s\"']+)")


@dataclass(frozen=True)
class TerminalSandboxRequest:
    command: str
    cwd: Path
    task_id: str
    timeout_seconds: int
    max_output_bytes: int
    profile: TerminalSandboxProfile | None


@dataclass(frozen=True)
class TerminalSandboxResult:
    exit_code: int | None
    output: str
    stdout: str
    stderr: str
    backend: str
    backend_status: str
    fallback_chain: list[str]
    degraded_reason: str | None
    timed_out: bool
    duration_ms: int
    output_truncated: bool
    resource_usage: dict[str, Any] = field(default_factory=dict)
    cleanup: dict[str, Any] = field(default_factory=dict)
    env_policy: dict[str, Any] = field(default_factory=dict)
    filesystem_policy: dict[str, Any] = field(default_factory=dict)
    network_policy: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)

    def sandbox_profile_result(self) -> dict[str, Any]:
        return {
            "type": "local_artifact",
            "os_sandbox": self.backend == "windows_job_object",
            "os_sandbox_backend": self.backend,
            "backend_status": self.backend_status,
            "fallback_chain": self.fallback_chain,
            "accepted_risk": self.degraded_reason,
            "degraded_reason": self.degraded_reason,
            "resource_usage": self.resource_usage,
            "cleanup": self.cleanup,
        }

    def diagnostic_summary(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "backend_status": self.backend_status,
            "fallback_chain": self.fallback_chain,
            "degraded_reason": self.degraded_reason,
            "timed_out": self.timed_out,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "output_truncated": self.output_truncated,
            "resource_usage": self.resource_usage,
            "cleanup": self.cleanup,
            "env_policy": self.env_policy,
            "filesystem_policy": self.filesystem_policy,
            "network_policy": self.network_policy,
            "reason_codes": self.reason_codes,
        }


@dataclass(frozen=True)
class SandboxBackendUnavailable(Exception):
    reason: str


class TerminalSandboxRunner:
    def __init__(self) -> None:
        self._backend_override: str | None = None

    def set_backend_override(self, backend: str | None) -> None:
        if backend is not None and backend not in SANDBOX_BACKENDS and backend not in LEGACY_SANDBOX_BACKENDS:
            raise ValueError(f"Unsupported sandbox backend: {backend}")
        self._backend_override = backend

    def status(self, profile: TerminalSandboxProfile | None) -> dict[str, Any]:
        requested = self._requested_backend(profile)
        selected, degraded_reason, fallback_chain = self._select_backend(requested)
        return {
            "active_backend": selected,
            "requested_backend": requested,
            "available_backends": self._available_backends(),
            "fallback_reason": degraded_reason,
            "fallback_chain": fallback_chain,
            "profile": profile.model_dump(mode="json") if profile else None,
            "limits": {
                "timeout_seconds": profile.timeout_seconds if profile else 30,
                "max_output_bytes": profile.max_output_bytes if profile else 200000,
                "active_process_limit": 16,
                "memory_limit_bytes": 512 * 1024 * 1024,
            },
            "degraded_backend": selected == "policy_guard",
            "low_integrity_status": "degraded_not_enabled",
        }

    async def run(self, request: TerminalSandboxRequest) -> TerminalSandboxResult:
        preflight = _preflight_command(request.command, request.cwd, request.profile)
        requested = self._requested_backend(request.profile)
        selected, degraded_reason, fallback_chain = self._select_backend(requested)
        env = _minimal_env(request)
        try:
            if selected == "windows_job_object":
                return await asyncio.to_thread(
                    _run_windows_job_object_sync,
                    request,
                    env,
                    preflight,
                    fallback_chain,
                    degraded_reason,
                )
            return await asyncio.to_thread(
                _run_policy_guard_sync,
                request,
                env,
                preflight,
                fallback_chain,
                degraded_reason or "os_sandbox_fallback_policy_guard",
            )
        except SandboxBackendUnavailable as exc:
            fallback = [*fallback_chain]
            if "policy_guard" not in fallback:
                fallback.append("policy_guard")
            return await asyncio.to_thread(
                _run_policy_guard_sync,
                request,
                env,
                preflight,
                fallback,
                exc.reason,
            )

    def _requested_backend(self, profile: TerminalSandboxProfile | None) -> str:
        if self._backend_override is not None:
            return self._normalize_backend(self._backend_override)
        if profile is not None and profile.os_sandbox_backend:
            return self._normalize_backend(profile.os_sandbox_backend)
        return self._normalize_backend(default_os_sandbox_backend())

    def _normalize_backend(self, backend: str) -> str:
        if backend == "disabled":
            return "policy_guard"
        if backend == "windows_low_integrity":
            return "container"
        return backend

    def _select_backend(self, requested: str) -> tuple[str, str | None, list[str]]:
        if requested == "windows_job_object":
            if os.name == "nt":
                return "windows_job_object", None, ["windows_job_object"]
            return (
                "policy_guard",
                "windows_job_object_unavailable_on_non_windows",
                ["windows_job_object", "policy_guard"],
            )
        if requested == "container":
            return (
                "policy_guard",
                "container_not_enabled",
                ["container", "policy_guard"],
            )
        return "policy_guard", "os_sandbox_fallback_policy_guard", ["policy_guard"]

    def _available_backends(self) -> list[dict[str, Any]]:
        return [
            {
                "backend": "windows_job_object",
                "available": os.name == "nt",
                "status": "available" if os.name == "nt" else "degraded",
            },
            {"backend": "container", "available": False, "status": "degraded_not_enabled"},
            {"backend": "policy_guard", "available": True, "status": "available"},
        ]


def default_os_sandbox_backend() -> str:
    if os.environ.get("CYCBER_TERMINAL_SANDBOX_BACKEND"):
        return os.environ["CYCBER_TERMINAL_SANDBOX_BACKEND"].strip()
    return "windows_job_object" if os.name == "nt" else "policy_guard"


def command_network_policy(command: str) -> dict[str, Any]:
    if not NETWORK_RE.search(command):
        return {
            "category": "terminal_command",
            "network_profile": "offline",
            "reason_codes": [],
        }
    if NETWORK_WRITE_RE.search(command):
        return {
            "category": "network_write",
            "network_profile": "external_write_requires_approval",
            "reason_codes": ["terminal_network_external_write_requires_approval"],
        }
    return {
        "category": "network_read",
        "network_profile": "external_read_requires_approval",
        "reason_codes": ["terminal_network_external_read_detected"],
    }


def _preflight_command(
    command: str,
    cwd: Path,
    profile: TerminalSandboxProfile | None,
) -> dict[str, Any]:
    if not command.strip():
        raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "终端命令不能为空", status_code=422)
    cwd = cwd.resolve()
    if not cwd.exists():
        cwd.mkdir(parents=True, exist_ok=True)
    reason_codes: list[str] = ["terminal_sandbox_preflight"]
    if TRAVERSAL_RE.search(command):
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "终端命令包含路径穿越，已拒绝",
            status_code=403,
            details={"reason_codes": ["terminal_path_traversal_denied"]},
        )
    if SENSITIVE_PATH_RE.search(command):
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "终端命令包含敏感路径，已拒绝",
            status_code=403,
            details={"reason_codes": ["terminal_sensitive_path_denied"]},
        )
    if SYMLINK_RE.search(command):
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "终端命令包含符号链接逃逸风险，已拒绝",
            status_code=403,
            details={"reason_codes": ["terminal_symlink_escape_denied"]},
        )
    executable = _first_executable(command)
    denied_items = profile.denied_executables if profile else []
    allowed_items = profile.allowed_executables if profile else []
    denied = {_normalize_executable(item) for item in denied_items}
    allowed = {_normalize_executable(item) for item in allowed_items}
    if executable in denied:
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "终端命令使用了禁用 executable，已拒绝",
            status_code=403,
            details={"reason_codes": ["terminal_executable_denied"]},
        )
    if allowed and executable and executable not in allowed:
        network = command_network_policy(command)
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "终端命令 executable 未在 sandbox allowlist 中",
            status_code=403,
            details={
                "reason_codes": [
                    "terminal_executable_not_allowed",
                    *network["reason_codes"],
                ],
            },
        )
    network = command_network_policy(command)
    reason_codes.extend(network["reason_codes"])
    return {
        "executable": executable,
        "reason_codes": reason_codes,
        "filesystem_policy": {
            "sandbox_root": "task_artifact_sandbox",
            "path_traversal": "deny",
            "symlink_escape": "deny",
            "sensitive_paths": "deny",
        },
        "network_policy": network,
    }


def _minimal_env(request: TerminalSandboxRequest) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in SAFE_ENV_KEYS:
        value = os.environ.get(key)
        if value and not SECRET_ENV_RE.search(key):
            env[key] = value
    python_dir = str(Path(sys.executable).resolve().parent)
    path_entries = [item for item in str(env.get("PATH") or "").split(os.pathsep) if item]
    if python_dir not in path_entries:
        env["PATH"] = os.pathsep.join([python_dir, *path_entries]) if path_entries else python_dir
    env["CYCBER_TASK_ID"] = request.task_id
    env["CYCBER_SANDBOX_ROOT"] = str(request.cwd)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _run_windows_job_object_sync(
    request: TerminalSandboxRequest,
    env: dict[str, str],
    preflight: dict[str, Any],
    fallback_chain: list[str],
    degraded_reason: str | None,
) -> TerminalSandboxResult:
    if os.name != "nt":
        raise SandboxBackendUnavailable("windows_job_object_unavailable_on_non_windows")
    try:
        job = _create_windows_job_object()
    except Exception as exc:  # pragma: no cover - depends on OS API
        raise SandboxBackendUnavailable(str(redact(str(exc))) or "job_object_unavailable") from exc
    started = time.perf_counter()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            request.command,
            cwd=str(request.cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        try:
            _assign_process_to_job(job, proc)
        except Exception as exc:  # pragma: no cover - depends on OS API
            _terminate_process_best_effort(proc)
            raise SandboxBackendUnavailable(
                str(redact(str(exc))) or "job_object_assign_failed"
            ) from exc
        return _communicate_with_timeout(
            request=request,
            proc=proc,
            env=env,
            started=started,
            backend="windows_job_object",
            backend_status="active",
            fallback_chain=fallback_chain,
            degraded_reason=degraded_reason,
            preflight=preflight,
            job_handle=job,
        )
    finally:
        _close_windows_handle(job)


def _run_policy_guard_sync(
    request: TerminalSandboxRequest,
    env: dict[str, str],
    preflight: dict[str, Any],
    fallback_chain: list[str],
    degraded_reason: str | None,
) -> TerminalSandboxResult:
    started = time.perf_counter()
    proc = subprocess.Popen(
        request.command,
        cwd=str(request.cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
    )
    return _communicate_with_timeout(
        request=request,
        proc=proc,
        env=env,
        started=started,
        backend="policy_guard",
        backend_status="degraded",
        fallback_chain=fallback_chain,
        degraded_reason=degraded_reason,
        preflight=preflight,
        job_handle=None,
    )


def _communicate_with_timeout(
    *,
    request: TerminalSandboxRequest,
    proc: subprocess.Popen[bytes],
    env: dict[str, str],
    started: float,
    backend: str,
    backend_status: str,
    fallback_chain: list[str],
    degraded_reason: str | None,
    preflight: dict[str, Any],
    job_handle: Any | None,
) -> TerminalSandboxResult:
    timed_out = False
    cleanup = {"kill_tree_attempted": False, "terminated": False}
    try:
        stdout, stderr = proc.communicate(timeout=request.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        cleanup["kill_tree_attempted"] = True
        if job_handle is not None:
            _terminate_windows_job(job_handle)
        else:
            _terminate_process_best_effort(proc)
        cleanup["terminated"] = True
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stdout, stderr = b"", b""
    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    combined = f"{stdout_text}\n{stderr_text}"
    truncated = len(combined.encode("utf-8")) > request.max_output_bytes
    output = combined[: request.max_output_bytes]
    return TerminalSandboxResult(
        exit_code=proc.returncode,
        output=output,
        stdout=stdout_text[: request.max_output_bytes],
        stderr=stderr_text[: request.max_output_bytes],
        backend=backend,
        backend_status=backend_status,
        fallback_chain=fallback_chain,
        degraded_reason=degraded_reason,
        timed_out=timed_out,
        duration_ms=duration_ms,
        output_truncated=truncated,
        resource_usage={
            "wall_time_ms": duration_ms,
            "process_id": proc.pid,
            "active_process_limit": 16 if backend == "windows_job_object" else None,
            "memory_limit_bytes": 512 * 1024 * 1024 if backend == "windows_job_object" else None,
        },
        cleanup=cleanup,
        env_policy={
            "inherit": "minimal_allowlist",
            "secret_env": "deny",
            "allowed_keys": sorted(env),
        },
        filesystem_policy=preflight["filesystem_policy"],
        network_policy=preflight["network_policy"],
        reason_codes=list(preflight["reason_codes"]),
    )


def _create_windows_job_object() -> Any:
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise RuntimeError(_windows_error("CreateJobObjectW"))
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    job_object_extended_limit_information = 9
    job_object_limit_kill_on_job_close = 0x00002000
    job_object_limit_active_process = 0x00000008
    job_object_limit_process_memory = 0x00000100
    info.BasicLimitInformation.LimitFlags = (
        job_object_limit_kill_on_job_close
        | job_object_limit_active_process
        | job_object_limit_process_memory
    )
    info.BasicLimitInformation.ActiveProcessLimit = 16
    info.ProcessMemoryLimit = 512 * 1024 * 1024
    ok = kernel32.SetInformationJobObject(
        job,
        job_object_extended_limit_information,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        error = _windows_error("SetInformationJobObject")
        _close_windows_handle(job)
        raise RuntimeError(error)
    return job


def _assign_process_to_job(job: Any, proc: subprocess.Popen[bytes]) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    handle = getattr(proc, "_handle", None)
    if handle is None:
        raise RuntimeError("process_handle_unavailable")
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(handle)):
        raise RuntimeError(_windows_error("AssignProcessToJobObject"))


def _terminate_windows_job(job: Any) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject(job, 1)


def _close_windows_handle(handle: Any) -> None:
    if os.name != "nt" or not handle:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def _windows_error(prefix: str) -> str:
    import ctypes

    error = ctypes.get_last_error()
    return f"{prefix}_failed:{error}:{ctypes.FormatError(error)}"


def _terminate_process_best_effort(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.kill()
    except OSError:
        return


def _first_executable(command: str) -> str:
    match = SHELL_META_EXE_RE.search(command)
    if not match:
        return ""
    return _normalize_executable(match.group(1))


def _normalize_executable(value: str) -> str:
    value = value.strip().strip("\"'").lower()
    name = Path(value).name
    for suffix in [".exe", ".cmd", ".bat", ".ps1"]:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name
