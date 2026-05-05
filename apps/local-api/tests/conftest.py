from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient
from pytest import Item

ROOT_DIR = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as test_client:
        yield test_client


def pytest_collection_modifyitems(items: list[Item]) -> None:
    for item in items:
        nodeid = item.nodeid.lower().replace("\\", "/")
        markers = _markers_for(nodeid)
        for marker in sorted(markers):
            item.add_marker(getattr(pytest.mark, marker))


def _markers_for(nodeid: str) -> set[str]:
    markers = {"api"}
    if "test_phase" in nodeid or "test_release" in nodeid:
        markers.add("integration")
    if "eval" in nodeid:
        markers.add("eval")
    if (
        "release" in nodeid
        or "diagnostic" in nodeid
        or "benchmark" in nodeid
        or "phase29" in nodeid
        or "phase30" in nodeid
        or "phase31" in nodeid
        or "phase33" in nodeid
        or "phase34" in nodeid
        or "phase35" in nodeid
        or "phase36" in nodeid
        or "phase37" in nodeid
        or "phase38" in nodeid
        or "phase39" in nodeid
        or "phase40" in nodeid
        or "phase41" in nodeid
        or "phase42" in nodeid
        or "phase43" in nodeid
        or "phase44" in nodeid
        or "phase45" in nodeid
        or "phase46" in nodeid
        or "phase47" in nodeid
        or "phase48" in nodeid
        or "phase49" in nodeid
        or "phase50" in nodeid
        or "phase51" in nodeid
        or "phase53" in nodeid
        or "phase54" in nodeid
        or "phase55" in nodeid
        or "phase56" in nodeid
        or "phase57" in nodeid
        or "phase66" in nodeid
    ):
        markers.add("release")
    if (
        "phase17" in nodeid
        or "phase30" in nodeid
        or "phase31" in nodeid
        or "phase33" in nodeid
        or "phase34" in nodeid
        or "phase35" in nodeid
        or "phase41" in nodeid
        or "phase42" in nodeid
        or "phase43" in nodeid
        or "phase45" in nodeid
        or "phase47" in nodeid
        or "phase50" in nodeid
        or "phase51" in nodeid
        or "phase53" in nodeid
        or "phase54" in nodeid
        or "phase55" in nodeid
        or "phase56" in nodeid
        or "phase57" in nodeid
        or "phase66" in nodeid
        or "chat_main_chain" in nodeid
    ):
        markers.add("chat_main_chain")
    if any(
        token in nodeid
        for token in [
            "security",
            "safety",
            "approval",
            "permission",
            "secret",
            "dlp",
            "execution_boundary",
            "phase21",
            "phase27",
            "phase28",
            "phase29",
            "phase30",
            "phase31",
            "phase33",
            "phase34",
            "phase35",
            "phase36",
            "phase37",
            "phase38",
            "phase39",
            "phase40",
            "phase41",
            "phase42",
            "phase43",
            "phase44",
            "phase45",
            "phase46",
            "phase47",
            "phase48",
            "phase49",
            "phase50",
            "phase51",
            "phase53",
            "phase54",
            "phase55",
            "phase56",
            "phase57",
            "phase66",
        ]
    ):
        markers.add("security")
    if any(
        token in nodeid
        for token in [
            "release_report",
            "release_gate",
            "phase19_eval_and_release",
            "phase20_knowledge_rerank",
            "phase21_eval_and_release",
            "phase22_replay_eval",
            "phase23",
            "phase24",
            "phase25",
            "phase26",
            "phase27",
            "phase28",
            "phase29",
            "phase30",
            "phase31",
            "phase33",
            "phase34",
            "phase35",
            "phase36",
            "phase37",
            "phase38",
            "phase39",
            "phase40",
            "phase41",
            "phase42",
            "phase43",
            "phase44",
            "phase45",
            "phase46",
            "phase47",
            "phase48",
            "phase49",
            "phase50",
            "phase51",
            "phase53",
            "phase54",
            "phase55",
            "phase56",
            "phase57",
            "phase66",
        ]
    ):
        markers.add("slow")
    return markers
