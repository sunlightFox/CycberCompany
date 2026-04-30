from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from cycber_cli.config import (
    DEFAULT_BASE_URL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    find_repo_root,
    merged_pythonpath,
)


@dataclass(frozen=True)
class ServerStatus:
    healthy: bool
    base_url: str
    status: str
    detail: dict[str, Any]


class ServerManager:
    def __init__(
        self,
        *,
        root: Path | None = None,
        base_url: str = DEFAULT_BASE_URL,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        log_dir: Path | None = None,
    ) -> None:
        self.root = root or find_repo_root()
        self.base_url = base_url.rstrip("/")
        self.host = host
        self.port = port
        self.log_dir = log_dir or self.root / "data" / "cli" / "logs"

    async def status(self) -> ServerStatus:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{self.base_url}/health")
            if response.status_code == 200:
                return ServerStatus(True, self.base_url, "healthy", response.json())
            return ServerStatus(
                False,
                self.base_url,
                "unhealthy",
                {"status_code": response.status_code},
            )
        except Exception as exc:
            return ServerStatus(False, self.base_url, "unreachable", {"error": str(exc)})

    async def ensure_running(
        self,
        *,
        autostart: bool = True,
        timeout_seconds: int = 30,
    ) -> ServerStatus:
        current = await self.status()
        if current.healthy or not autostart:
            return current
        if _port_open(self.host, self.port):
            return ServerStatus(
                False,
                self.base_url,
                "port_in_use",
                {"reason": "port is open but /health is not healthy"},
            )
        self.start_background()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current = await self.status()
            if current.healthy:
                return current
            time.sleep(0.5)
        return ServerStatus(
            False,
            self.base_url,
            "start_timeout",
            {"timeout_seconds": timeout_seconds},
        )

    def start_background(self) -> subprocess.Popen[Any]:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"local-api-{time.strftime('%Y%m%dT%H%M%S')}.log"
        env = os.environ.copy()
        env["CYCBER_ROOT"] = str(self.root)
        env["PYTHONPATH"] = merged_pythonpath(self.root)
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            str(self.root / "apps" / "local-api"),
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log_file = log_path.open("a", encoding="utf-8")
        return subprocess.Popen(
            command,
            cwd=str(self.root),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0
