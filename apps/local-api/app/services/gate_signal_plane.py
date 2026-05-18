from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


_ROOT_DIR = Path(__file__).resolve().parents[4]
_CONFIG_PATH = _ROOT_DIR / "config" / "gate_signal_plane.json"


@dataclass(frozen=True)
class GateSignalProfile:
    profile: str
    suite_id: str
    suite_name: str
    signal_suites: list[dict[str, Any]]


@lru_cache(maxsize=1)
def _load_raw_config() -> dict[str, Any]:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def gate_signal_plane_path() -> Path:
    return _CONFIG_PATH


def gate_signal_plane_contract_version() -> str:
    return str(_load_raw_config().get("check_contract_version") or "")


def load_gate_signal_profile(profile: str) -> GateSignalProfile | None:
    raw = _load_raw_config()
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        return None
    entry = profiles.get(profile)
    if not isinstance(entry, dict):
        return None
    signal_suites = [
        item
        for item in entry.get("signal_suites", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]
    return GateSignalProfile(
        profile=profile,
        suite_id=str(entry.get("suite_id") or ""),
        suite_name=str(entry.get("suite_name") or ""),
        signal_suites=signal_suites,
    )


def smoke_signal_suites() -> list[dict[str, Any]]:
    profile = load_gate_signal_profile("smoke")
    return list(profile.signal_suites) if profile is not None else []


def smoke_signal_suite_paths() -> list[str]:
    return [str(item["path"]) for item in smoke_signal_suites()]


def smoke_signal_phase_keys() -> list[str]:
    keys: list[str] = []
    for item in smoke_signal_suites():
        phase_key = item.get("phase_key")
        if isinstance(phase_key, str) and phase_key:
            keys.append(phase_key)
    return keys


def smoke_signal_suite_summary() -> dict[str, Any]:
    profile = load_gate_signal_profile("smoke")
    if profile is None:
        return {
            "profile": "smoke",
            "suite_id": "",
            "suite_name": "",
            "check_contract_version": gate_signal_plane_contract_version(),
            "signal_suites": [],
            "paths": [],
            "phase_keys": [],
        }
    return {
        "profile": profile.profile,
        "suite_id": profile.suite_id,
        "suite_name": profile.suite_name,
        "check_contract_version": gate_signal_plane_contract_version(),
        "signal_suites": list(profile.signal_suites),
        "paths": [str(item["path"]) for item in profile.signal_suites],
        "phase_keys": smoke_signal_phase_keys(),
    }
