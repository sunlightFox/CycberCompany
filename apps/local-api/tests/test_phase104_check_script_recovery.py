from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
CHECK_SCRIPT = ROOT_DIR / "scripts" / "check.ps1"
NESTED_CHECK_GUARD = "CYCBER_SKIP_NESTED_CHECK_TESTS"


def test_phase104_check_script_is_valid_powershell() -> None:
    parse_command = (
        f"[void][scriptblock]::Create((Get-Content -Path '{CHECK_SCRIPT}' -Raw -Encoding UTF8)); "
        "'parse-ok'"
    )
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        parse_command,
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "parse-ok" in completed.stdout


def test_phase104_check_script_failure_writes_report() -> None:
    if os.environ.get(NESTED_CHECK_GUARD) == "1" or os.environ.get("CYCBER_RUNNING_CHECK_SMOKE") == "1":
        return

    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(CHECK_SCRIPT),
    ]
    env = dict(os.environ)
    env[NESTED_CHECK_GUARD] = "1"

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    combined_output = f"{completed.stdout}\n{completed.stderr}"

    assert completed.returncode != 0
    match = re.search(r"Check report:\s*(.+check-\d{8}T\d{6}Z\.json)", combined_output)
    assert match, combined_output

    report_path = Path(match.group(1).strip())
    assert report_path.exists(), report_path

    payload = json.loads(report_path.read_text(encoding="utf-8-sig"))

    assert payload["status"] == "failed"
    assert payload["profile"] == "full"
    assert payload["check_contract_version"] == "phase105.gate_signal_plane.v1"
    assert payload["signal_suites"] == []
    assert payload["commands"]
    assert payload["commands"][0]["name"] == "ruff"
    assert payload["commands"][0]["status"] == "failed"


def test_phase104_check_script_smoke_profile_runs_and_writes_report() -> None:
    if os.environ.get(NESTED_CHECK_GUARD) == "1" or os.environ.get("CYCBER_RUNNING_CHECK_SMOKE") == "1":
        return

    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(CHECK_SCRIPT),
        "-Profile",
        "smoke",
    ]

    env = dict(os.environ)
    env[NESTED_CHECK_GUARD] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    signal_config = {
        "check_contract_version": "phase105.gate_signal_plane.v1",
        "profiles": {
            "smoke": {
                "suite_id": "phase113_test_smoke",
                "suite_name": "Phase113 Test Smoke",
                "signal_suites": [
                    {
                        "suite_key": "response_composer_reasoning",
                        "path": "tests/test_response_composer_reasoning.py",
                        "kind": "foundation",
                    },
                    {
                        "suite_key": "phase104_check_report_contract",
                        "path": "apps/local-api/tests/test_phase104_check_report_contract.py",
                        "kind": "phase",
                        "phase_key": "phase104_check_report_contract",
                    },
                ],
            }
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fp:
        json.dump(signal_config, fp, ensure_ascii=False, indent=2)
        temp_signal_path = fp.name
    env["CYCBER_GATE_SIGNAL_CONFIG"] = temp_signal_path

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
    finally:
        Path(temp_signal_path).unlink(missing_ok=True)
    combined_output = f"{completed.stdout}\n{completed.stderr}"

    assert completed.returncode == 0, combined_output
    match = re.search(r"Check report:\s*(.+check-\d{8}T\d{6}Z\.json)", combined_output)
    assert match, combined_output

    report_path = Path(match.group(1).strip())
    assert report_path.exists(), report_path

    payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    assert payload["status"] == "passed"
    assert payload["profile"] == "smoke"
    assert payload["commands"]
    assert payload["commands"][0]["name"] == "pytest_smoke"
    assert payload["commands"][0]["status"] == "passed"
