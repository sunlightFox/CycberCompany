from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = BASE_DIR / "evidence"
RUNNER = BASE_DIR / "run_feishu_broad_round10_100_visible_quality_real_model_cases.py"
PROGRESS_PATH = EVIDENCE_DIR / "missing_casewise_python_progress.json"
TIMEOUT_SECONDS = 170


def _result_path(case_id: str) -> Path:
    return EVIDENCE_DIR / f"casewise_{case_id}_result.json"


def _write_progress(items: list[dict[str, object]]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_label": "FBR10-100-VISIBLE-REAL-20260523",
        "mode": "missing-casewise-python",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "completed": len(items),
        "items": items,
    }
    PROGRESS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, object]] = []
    for index in range(1, 101):
        case_id = f"FBR10-100-{index:03d}"
        if _result_path(case_id).exists():
            continue

        started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        stdout_path = EVIDENCE_DIR / f"missing_py_{case_id}.stdout.txt"
        stderr_path = EVIDENCE_DIR / f"missing_py_{case_id}.stderr.txt"
        command = [sys.executable, str(RUNNER), "--case-id", case_id, "--merge-existing"]
        exit_code: int | str
        try:
            completed = subprocess.run(
                command,
                cwd=str(BASE_DIR.parents[3]),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
            )
            stdout_path.write_text(completed.stdout, encoding="utf-8")
            stderr_path.write_text(completed.stderr, encoding="utf-8")
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            exit_code = "timeout"

        items.append(
            {
                "case_id": case_id,
                "exit_code": exit_code,
                "started_at": started_at,
                "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "result_exists": _result_path(case_id).exists(),
            }
        )
        _write_progress(items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
