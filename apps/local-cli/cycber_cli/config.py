from __future__ import annotations

import os
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "scripts").exists():
            return candidate
    env_root = os.environ.get("CYCBER_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return current


def pythonpath_entries(root: Path) -> list[Path]:
    return [
        root / "apps" / "local-cli",
        root / "apps" / "local-api",
        root / "packages" / "core-types",
        root / "services" / "asset-broker",
        root / "services" / "brain",
        root / "services" / "capability-graph",
        root / "services" / "chat-runtime",
        root / "services" / "context-gateway",
        root / "services" / "heart",
        root / "services" / "memory",
        root / "services" / "persona-engine",
        root / "services" / "response-composer",
        root / "services" / "safety",
        root / "services" / "shell-runtime",
        root / "services" / "skill-engine",
        root / "services" / "task-engine",
        root / "services" / "tools",
        root / "services" / "trace",
    ]


def merged_pythonpath(root: Path) -> str:
    existing = os.environ.get("PYTHONPATH", "")
    entries = [str(path) for path in pythonpath_entries(root)]
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)
