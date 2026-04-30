from __future__ import annotations

from pathlib import Path
from typing import Any

from cycber_cli.server import ServerManager


def test_phase32_server_manager_constructs_background_uvicorn_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        pid = 123

    def fake_popen(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append({"command": command, **kwargs})
        return FakeProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    manager = ServerManager(root=tmp_path, log_dir=tmp_path / "logs")

    process = manager.start_background()

    assert process.pid == 123
    assert calls
    command = calls[0]["command"]
    assert command[:3] == [command[0], "-m", "uvicorn"]
    assert "app.main:app" in command
    assert str(tmp_path / "apps" / "local-api") in command
    assert calls[0]["env"]["CYCBER_ROOT"] == str(tmp_path)
    assert (tmp_path / "logs").exists()
