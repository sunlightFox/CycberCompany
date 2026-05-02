from __future__ import annotations

import json

import pytest
from cycber_cli import app
from cycber_cli.output import print_payload


def test_phase32_cli_help_and_command_parser() -> None:
    parser = app.build_parser()
    args = parser.parse_args(["chat", "-m", "你好", "--json", "--no-stream"])

    assert args.command == "chat"
    assert args.message == "你好"
    assert args.json is True
    assert args.stream is False

    export = parser.parse_args(
        ["chat", "-m", "生成 Word", "--no-stream", "--export-dir", "out"]
    )
    assert str(export.export_dir) == "out"

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0


def test_phase32_cli_print_payload_survives_non_utf8_stdout(monkeypatch) -> None:
    class FakeBuffer:
        def __init__(self) -> None:
            self.written = b""

        def write(self, value: bytes) -> None:
            self.written += value

    class FakeStdout:
        encoding = "gbk"

        def __init__(self) -> None:
            self.buffer = FakeBuffer()

        def write(self, value: str) -> None:
            value.encode(self.encoding)

        def flush(self) -> None:
            return None

    fake_stdout = FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)

    print_payload({"text": "我先踩一下刹车 🙂"}, json_mode=True)

    assert b"\\U0001f642" in fake_stdout.buffer.written


def test_phase55_cli_skills_parser() -> None:
    parser = app.build_parser()
    search = parser.parse_args(["skills", "search", "draft", "--source", "clawhub", "--json"])
    assert search.command == "skills"
    assert search.autostart is True
    assert search.skills_command == "search"
    assert search.query == "draft"
    assert search.source == "clawhub"
    no_autostart = parser.parse_args(
        ["skills", "--no-autostart", "search", "draft", "--source", "clawhub"]
    )
    assert no_autostart.autostart is False
    install = parser.parse_args(
        [
            "skills",
            "install",
            "clawhub:official/content/local-draft",
            "--enable",
            "--grant-default",
        ]
    )
    assert install.skills_command == "install"
    assert install.enable is True
    assert install.grant_default is True
    grant = parser.parse_args(
        [
            "skills",
            "grant",
            "skill.clawhub-word-report.clawhub-word-report",
            "--tool",
            "office.word.generate",
        ]
    )
    assert grant.skills_command == "grant"
    assert grant.tools == ["office.word.generate"]


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


@pytest.mark.asyncio
async def test_phase58_cli_chat_outputs_artifacts_and_exports(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CYCBER_CLI_HOME", str(tmp_path / "home"))
    export_dir = tmp_path / "exports"

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
            assert payload["input"]["text"] == "生成 Word"
            return {"turn_id": "turn_1", "trace_id": "trc_1", "stream_url": None}

        async def turn_events(self, turn_id):
            assert turn_id == "turn_1"
            return [
                {"event_type": "response.delta", "payload": {"text": "Word 已完成。"}},
                {
                    "event_type": "response.completed",
                    "payload": {
                        "payload": {
                            "response_plan": {
                                "artifact_refs": [
                                    {
                                        "artifact_id": "art_1",
                                        "display_name": "report.docx",
                                        "content_type": (
                                            "application/vnd.openxmlformats-officedocument."
                                            "wordprocessingml.document"
                                        ),
                                        "download_url": "/api/artifacts/art_1/download",
                                    }
                                ]
                            }
                        }
                    },
                },
            ]

        async def download_artifact(self, artifact_id):
            assert artifact_id == "art_1"
            return b"docx-bytes", {"content-type": "application/octet-stream"}

    monkeypatch.setattr(app, "ServerManager", FakeManager)
    monkeypatch.setattr(app, "CycberApiClient", FakeClient)
    args = app.build_parser().parse_args(
        [
            "chat",
            "-m",
            "生成 Word",
            "--no-stream",
            "--json",
            "--export-dir",
            str(export_dir),
        ]
    )

    exit_code = await app._dispatch(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["artifacts"][0]["artifact_id"] == "art_1"
    assert payload["exported"][0]["display_name"] == "report.docx"
    assert (export_dir / "report.docx").read_bytes() == b"docx-bytes"


@pytest.mark.asyncio
async def test_phase55_cli_skills_search_and_install(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("CYCBER_CLI_HOME", str(tmp_path))

    class FakeManager:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def ensure_running(self, **kwargs):  # noqa: ANN003
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.installed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def search_skills(self, query, *, repository_id=None, limit=50):
            assert query == "draft"
            assert repository_id == "clawhub"
            return {"items": [{"package_ref": "official/content/local-draft"}]}

        async def install_skill(self, payload):
            assert payload["source_type"] == "repository_ref"
            assert payload["source_uri"] == "clawhub:official/content/local-draft"
            return {"bundle": {"bundle_id": "clawhub-local-draft"}}

        async def enable_plugin(self, bundle_id, actor_member_id="mem_xiaoyao"):
            assert bundle_id == "clawhub-local-draft"
            return {"status": "enabled"}

        async def grant_skill(self, skill_id, payload):
            assert skill_id == "skill.clawhub-local-draft.clawhub-local-draft"
            assert payload["allowed_tools"] == ["file.write"]
            return {"items": [{"skill_id": skill_id, "allowed_tools": payload["allowed_tools"]}]}

    monkeypatch.setattr(app, "ServerManager", FakeManager)
    monkeypatch.setattr(app, "CycberApiClient", FakeClient)

    search_args = app.build_parser().parse_args(
        ["skills", "search", "draft", "--source", "clawhub", "--json"]
    )
    assert await app._dispatch(search_args) == 0
    assert "official/content/local-draft" in capsys.readouterr().out

    install_args = app.build_parser().parse_args(
        ["skills", "install", "clawhub:official/content/local-draft", "--enable", "--json"]
    )
    assert await app._dispatch(install_args) == 0
    assert '"enabled"' in capsys.readouterr().out

    grant_args = app.build_parser().parse_args(
        [
            "skills",
            "grant",
            "skill.clawhub-local-draft.clawhub-local-draft",
            "--tool",
            "file.write",
            "--json",
        ]
    )
    assert await app._dispatch(grant_args) == 0
    assert "file.write" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_phase56_cli_skills_install_enable_and_grant_default(
    monkeypatch,
    tmp_path,
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
            self.granted = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def install_skill(self, payload):
            assert payload["source_type"] == "repository_ref"
            assert payload["source_uri"] == "clawhub:official/office/word-report"
            return {
                "bundle": {"bundle_id": "clawhub-word-report"},
                "skills": [
                    {
                        "skill_id": "skill.clawhub-word-report.clawhub-word-report",
                        "required_tools": ["office.word.generate"],
                    }
                ],
            }

        async def enable_plugin(self, bundle_id, actor_member_id="mem_xiaoyao"):
            assert bundle_id == "clawhub-word-report"
            assert actor_member_id == "mem_xiaoyao"
            return {"status": "enabled"}

        async def grant_skill(self, skill_id, payload):
            assert skill_id == "skill.clawhub-word-report.clawhub-word-report"
            assert payload["grant_scope"] == "curated_default"
            assert payload["allowed_tools"] == ["office.word.generate"]
            self.granted = True
            return {
                "items": [
                    {
                        "skill_id": skill_id,
                        "allowed_tools": payload["allowed_tools"],
                        "grant_scope": payload["grant_scope"],
                    }
                ]
            }

    monkeypatch.setattr(app, "ServerManager", FakeManager)
    monkeypatch.setattr(app, "CycberApiClient", FakeClient)

    install_args = app.build_parser().parse_args(
        [
            "skills",
            "install",
            "clawhub:official/office/word-report",
            "--enable",
            "--grant-default",
            "--json",
        ]
    )

    assert await app._dispatch(install_args) == 0
    output = capsys.readouterr().out
    assert "office.word.generate" in output
    assert "curated_default" in output
