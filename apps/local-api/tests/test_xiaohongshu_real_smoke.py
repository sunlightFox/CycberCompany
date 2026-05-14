from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_xiaohongshu_real_smoke.py"
_SPEC = importlib.util.spec_from_file_location("run_xiaohongshu_real_smoke", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_run_full_smoke_simulates_approval_and_retries(monkeypatch: Any) -> None:
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        _MODULE,
        "_create_plan",
        lambda *args, **kwargs: {
            "plan_id": "plan_1",
            "selected_asset_id": "ast_main",
            "approval_id": "apr_1",
        },
    )
    monkeypatch.setattr(_MODULE, "_ensure_selected_account", lambda *args, **kwargs: None)

    def fake_execute_adapter(*args: Any, plan_id: str, approval_id: str | None = None, **kwargs: Any) -> dict[str, object]:
        calls.append((plan_id, approval_id))
        if approval_id is None:
            return {
                "plan": {"plan_id": plan_id, "status": "awaiting_approval", "approval_id": "apr_1"},
                "execution": {"status": "awaiting_approval", "evidence": {"approval_id": "apr_1"}},
                "next_step": "approve_or_resume_after_human",
            }
        return {
            "plan": {"plan_id": plan_id, "status": "completed", "approval_id": "apr_1"},
            "execution": {
                "status": "completed",
                "evidence": {
                    "published_post_url": "https://example.test/notes/note-1",
                    "published_post_id": "note-1",
                    "publish_visible_text_confirmed": True,
                    "comment_visible_text_confirmed": True,
                    "publish_and_comment_both_confirmed": True,
                },
            },
            "next_step": None,
        }

    approvals: list[str] = []

    monkeypatch.setattr(_MODULE, "_execute_adapter", fake_execute_adapter)
    monkeypatch.setattr(
        _MODULE,
        "_simulate_approval",
        lambda *args, approval_id: approvals.append(approval_id) or {"status": "approved"},
    )

    payload, approval_path = _MODULE._run_full_smoke(
        object(),
        publish_text="body",
        title="title",
        comment_text="comment",
        target_post_url="",
        require_full_comment_flow=True,
        selected_asset_id="ast_main",
    )

    assert approval_path == "simulated"
    assert approvals == ["apr_1"]
    assert calls == [("plan_1", None), ("plan_1", "apr_1")]
    assert payload["plan"]["status"] == "completed"


def test_run_full_smoke_records_preapproved_path(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        _MODULE,
        "_create_plan",
        lambda *args, **kwargs: {
            "plan_id": "plan_2",
            "selected_asset_id": "ast_main",
            "approval_id": "apr_preapproved",
        },
    )
    monkeypatch.setattr(_MODULE, "_ensure_selected_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        _MODULE,
        "_execute_adapter",
        lambda *args, plan_id, approval_id=None, **kwargs: {
            "plan": {"plan_id": plan_id, "status": "completed", "approval_id": "apr_preapproved"},
            "execution": {
                "status": "completed",
                "evidence": {
                    "published_post_url": "https://example.test/notes/note-2",
                    "published_post_id": "note-2",
                    "publish_visible_text_confirmed": True,
                    "comment_visible_text_confirmed": True,
                    "publish_and_comment_both_confirmed": True,
                },
            },
            "next_step": None,
        },
    )
    monkeypatch.setattr(
        _MODULE,
        "_simulate_approval",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("approval should not run")),
    )

    payload, approval_path = _MODULE._run_full_smoke(
        object(),
        publish_text="body",
        title="title",
        comment_text="comment",
        target_post_url="",
        require_full_comment_flow=True,
        selected_asset_id="ast_main",
    )

    assert approval_path == "skipped_or_preapproved"
    assert payload["execution"]["status"] == "completed"


def test_summary_from_payload_includes_approval_path() -> None:
    payload = {
        "plan": {"status": "completed", "failure_reason": None, "approval_id": "apr_9"},
        "execution": {
            "status": "completed",
            "evidence": {
                "published_post_url": "https://example.test/notes/note-9",
                "published_post_id": "note-9",
                "publish_visible_text_confirmed": True,
                "comment_visible_text_confirmed": True,
                "publish_and_comment_both_confirmed": True,
                "recovery_evidence": {"reason_codes": ["ok"]},
            },
        },
    }
    summary = _MODULE._summary_from_payload(payload, approval_path="simulated")
    assert summary == {
        "plan_status": "completed",
        "execution_status": "completed",
        "failure_reason": None,
        "approval_path": "simulated",
        "approval_id": "apr_9",
        "published_post_url": "https://example.test/notes/note-9",
        "published_post_id": "note-9",
        "publish_visible_text_confirmed": True,
        "comment_visible_text_confirmed": True,
        "publish_and_comment_both_confirmed": True,
        "recovery_reason_codes": ["ok"],
    }
