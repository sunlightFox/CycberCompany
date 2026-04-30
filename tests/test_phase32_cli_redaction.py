from __future__ import annotations

import json

from cycber_cli.redaction import redact
from cycber_cli.state import CliState


def test_phase32_cli_redacts_terminal_output_and_nested_payloads() -> None:
    payload = {
        "message": (
            "token=sk-phase32secretvalue password=hunter2 "
            "cookie=sessionid private_key=abc C:\\Users\\Administrator\\secret.txt"
        ),
        "api_key": "sk-should-not-print",
    }

    redacted = json.dumps(redact(payload), ensure_ascii=False)

    assert "sk-phase32secretvalue" not in redacted
    assert "hunter2" not in redacted
    assert "sessionid" not in redacted
    assert "C:\\Users\\Administrator" not in redacted
    assert "[REDACTED]" in redacted


def test_phase32_cli_state_saves_only_non_sensitive_known_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CYCBER_CLI_HOME", str(tmp_path))
    state = CliState(
        conversation_id="conv_default_xiaoyao",
        member_id="mem_xiaoyao",
        session_id="cli_test",
        last_turn_id="turn_1",
        last_trace_id="trc_1",
    )

    state.save()
    raw = (tmp_path / "state.json").read_text(encoding="utf-8")
    loaded = CliState.load()

    assert "secret" not in raw.lower()
    assert loaded.conversation_id == "conv_default_xiaoyao"
    assert loaded.member_id == "mem_xiaoyao"
