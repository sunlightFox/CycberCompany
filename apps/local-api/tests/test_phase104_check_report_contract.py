from __future__ import annotations

import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
CHECK_SCRIPT = ROOT_DIR / "scripts" / "check.ps1"
SIGNAL_CONFIG = ROOT_DIR / "config" / "gate_signal_plane.json"


def test_phase104_check_script_keeps_command_matrix_and_gate_wiring() -> None:
    content = CHECK_SCRIPT.read_text(encoding="utf-8")
    signal_config = json.loads(SIGNAL_CONFIG.read_text(encoding="utf-8"))
    smoke_paths = [item["path"] for item in signal_config["profiles"]["smoke"]["signal_suites"]]

    for profile in [
        "-Profile full",
        "-Profile smoke",
        "-Profile fast",
        "-Profile api",
        "-Profile security",
        "-Profile release",
    ]:
        assert profile in content

    for marker in [
        "command_matrix",
        "New-SmokePytestArgs",
        "Get-GateSignalPlaneConfig",
        "Get-GateSignalProfile",
        "Get-ProfileSignalSuites",
        "Invoke-StaticChecks",
        "Resolve-ChatDocsDir",
        "Resolve-ChatDocFile",
        "Write-CheckReport",
        "check_contract_version",
        "signal_suites",
        "Invoke-ChatMainChainIssueGate",
        "Invoke-PowerChatIssueGate",
        "Invoke-NaturalChatIssueGate",
        "Invoke-QualityChatIssueGate",
        "Invoke-Phase68PromptResidualGate",
        "Invoke-Phase68VisibleLeakageGate",
        "CHAT-E2E-POWER-FIX",
        "CHAT-E2E-QUALITY-FIX",
        "run_chat_main_chain_power_cases.py",
        "run_chat_natural_interaction_benchmark.py",
    ]:
        assert marker in content

    assert "config\\gate_signal_plane.json" in content
    assert smoke_paths

    for marker in [
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
        '$env:PYTHONIOENCODING = "utf-8"',
        '$env:PYTHONUTF8 = "1"',
        'data\\check-reports',
    ]:
        assert marker in content
