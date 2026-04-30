from __future__ import annotations

import pytest
from cycber_cli import app


def test_phase32_cli_help_and_command_parser() -> None:
    parser = app.build_parser()
    args = parser.parse_args(["chat", "-m", "你好", "--json", "--no-stream"])

    assert args.command == "chat"
    assert args.message == "你好"
    assert args.json is True
    assert args.stream is False

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0


@pytest.mark.asyncio
async def test_phase32_cli_chat_command_uses_api_client_and_prints_json(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CYCBER_CLI_HOME", str(tmp_path))

    class FakeManager:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def ensure_running(self, **kwargs):  # noqa: ANN003
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def conversations(self):
            return [{"conversation_id": "conv_default_xiaoyao", "primary_member_id": "mem_xiaoyao"}]

        async def create_turn(self, payload):
            assert payload["session_id"].startswith("cli_")
            return {"turn_id": "turn_1", "trace_id": "trc_1", "stream_url": None}

        async def turn_events(self, turn_id):
            assert turn_id == "turn_1"
            return [{"event_type": "response.delta", "payload": {"text": "CLI 回复"}}]

    monkeypatch.setattr(app, "ServerManager", FakeManager)
    monkeypatch.setattr(app, "CycberApiClient", FakeClient)
    args = app.build_parser().parse_args(["chat", "-m", "你好", "--no-stream", "--json"])

    exit_code = await app._dispatch(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"text": "CLI 回复"' in output
