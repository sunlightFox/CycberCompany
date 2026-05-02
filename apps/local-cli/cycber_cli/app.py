from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from cycber_cli.chat import send_message
from cycber_cli.config import DEFAULT_BASE_URL, DEFAULT_HOST, DEFAULT_PORT, find_repo_root
from cycber_cli.http_client import ApiError, CycberApiClient
from cycber_cli.output import compact_turn, print_payload
from cycber_cli.repl import run_repl
from cycber_cli.server import ServerManager
from cycber_cli.state import CliState


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except ApiError as exc:
        print_payload(
            {"error": exc.payload, "status_code": exc.status_code},
            json_mode=getattr(args, "json", False),
        )
        return 1
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cycber", description="Cycber local CLI")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    sub = parser.add_subparsers(dest="command")
    _chat_parser(sub)
    sub.add_parser("status")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--autostart", action="store_true")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--foreground", action="store_true")
    serve.add_argument("--log-dir", type=Path)
    conversations = sub.add_parser("conversations")
    conv_sub = conversations.add_subparsers(dest="conversation_command", required=True)
    conv_sub.add_parser("list")
    conv_use = conv_sub.add_parser("use")
    conv_use.add_argument("conversation_id")
    conv_show = conv_sub.add_parser("show")
    conv_show.add_argument("conversation_id")
    turns = sub.add_parser("turns")
    turn_sub = turns.add_subparsers(dest="turn_command", required=True)
    for name in ["show", "events", "replay", "brain", "semantic", "quality"]:
        item = turn_sub.add_parser(name)
        item.add_argument("turn_id")
    traces = sub.add_parser("traces")
    trace_sub = traces.add_subparsers(dest="trace_command", required=True)
    trace_show = trace_sub.add_parser("show")
    trace_show.add_argument("trace_id")
    _skills_parser(sub)
    _tasks_parser(sub)
    sub.add_parser("config")
    return parser


def _chat_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    chat = sub.add_parser("chat")
    chat.add_argument("--message", "-m")
    chat.add_argument("--interactive", "-i", action="store_true")
    chat.add_argument("--conversation")
    chat.add_argument("--member")
    chat.add_argument("--session")
    chat.add_argument("--stream", dest="stream", action="store_true", default=True)
    chat.add_argument("--no-stream", dest="stream", action="store_false")
    chat.add_argument("--diagnostics", action="store_true")
    chat.add_argument("--json", action="store_true")
    chat.add_argument("--export-dir", type=Path)
    chat.add_argument("--autostart", dest="autostart", action="store_true", default=True)
    chat.add_argument("--no-autostart", dest="autostart", action="store_false")
    chat.add_argument("--timeout", type=int, default=180)


def _skills_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    skills = sub.add_parser("skills")
    skills.add_argument("--autostart", dest="autostart", action="store_true", default=True)
    skills.add_argument("--no-autostart", dest="autostart", action="store_false")
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    sources = skills_sub.add_parser("sources")
    sources_sub = sources.add_subparsers(dest="sources_command", required=True)
    sources_sub.add_parser("list")
    refresh = sources_sub.add_parser("refresh")
    refresh.add_argument("repository_id")
    add = sources_sub.add_parser("add")
    add.add_argument("repository_id")
    add.add_argument("--display-name", required=True)
    add.add_argument("--index-uri", required=True)
    add.add_argument("--provider", default="index_json")
    add.add_argument("--priority", type=int, default=100)
    add.add_argument("--default", action="store_true")
    disable = sources_sub.add_parser("disable")
    disable.add_argument("repository_id")
    search = skills_sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--source")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--json", action="store_true")
    install = skills_sub.add_parser("install")
    install.add_argument("ref")
    install.add_argument("--source")
    install.add_argument("--preview", action="store_true")
    install.add_argument("--enable", action="store_true")
    install.add_argument("--grant-default", action="store_true")
    install.add_argument("--json", action="store_true")
    install.add_argument("--type", dest="source_type")
    install.add_argument("--checksum")
    install.add_argument("--bundle-id")
    grant = skills_sub.add_parser("grant")
    grant.add_argument("skill_id")
    grant.add_argument("--tool", dest="tools", action="append", default=[])
    grant.add_argument("--member", default="mem_xiaoyao")
    grant.add_argument("--json", action="store_true")


def _tasks_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tasks = sub.add_parser("tasks")
    tasks.add_argument("--autostart", dest="autostart", action="store_true", default=True)
    tasks.add_argument("--no-autostart", dest="autostart", action="store_false")
    tasks.add_argument("--timeout", type=int, default=180)
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    run = tasks_sub.add_parser("run")
    run.add_argument("--goal", required=True)
    run.add_argument("--skill-id", required=True)
    run.add_argument("--content")
    run.add_argument("--input-json")
    run.add_argument("--export-dir", type=Path)
    run.add_argument("--json", action="store_true")


async def _dispatch(args: argparse.Namespace) -> int:
    command = args.command or "chat"
    state = CliState.load()
    if getattr(args, "base_url", None):
        state.base_url = args.base_url
    manager = ServerManager(base_url=state.base_url)
    if command in {
        "chat",
        "status",
        "doctor",
        "conversations",
        "turns",
        "traces",
        "skills",
        "tasks",
    }:
        autostart = bool(getattr(args, "autostart", False))
        await manager.ensure_running(autostart=autostart)
    timeout = float(getattr(args, "timeout", 30))
    async with CycberApiClient(state.base_url, timeout=timeout) as client:
        if command == "chat":
            return await _run_chat(args, client, state)
        if command == "status":
            print_payload({"state": state.__dict__, "health": await client.health()})
            return 0
        if command == "doctor":
            print_payload(
                {
                    "root": str(find_repo_root()),
                    "health": await client.health(),
                    "full_health": await _optional(client.full_health),
                    "runtime_contracts": await _optional(client.runtime_contracts),
                }
            )
            return 0
        if command == "conversations":
            return await _run_conversations(args, client, state)
        if command == "turns":
            return await _run_turns(args, client)
        if command == "traces":
            print_payload(await client.trace(args.trace_id))
            return 0
        if command == "skills":
            return await _run_skills(args, client)
        if command == "tasks":
            return await _run_tasks(args, client)
        if command == "config":
            print_payload(state.__dict__)
            return 0
    if command == "serve":
        manager = ServerManager(host=args.host, port=args.port, log_dir=args.log_dir)
        if args.foreground:
            print_payload({"command": "use scripts/dev.ps1 for foreground local-api"})
            return 0
        manager.start_background()
        print_payload((await manager.ensure_running(autostart=False)).__dict__)
        return 0
    return 0


async def _run_tasks(args: argparse.Namespace, client: CycberApiClient) -> int:
    if args.tasks_command == "run":
        skill_input = _task_skill_input(args)
        task = await client.create_task(
            {
                "owner_member_id": "mem_xiaoyao",
                "goal": args.goal,
                "mode_hint": "workflow",
                "constraints": {"skill_id": args.skill_id, "skill_input": skill_input},
                "auto_start": True,
            }
        )
        if task.get("status") == "created":
            task_id = str(task["task_id"])
            task = await client.start_task(task_id)
        artifacts = await client.task_artifacts(str(task["task_id"]))
        exported = _export_artifacts(artifacts.get("items", []), args.export_dir)
        print_payload(
            {
                "task": {
                    "task_id": task.get("task_id"),
                    "status": task.get("status"),
                    "title": task.get("title"),
                },
                "artifacts": artifacts.get("items", []),
                "exported": exported,
            },
            json_mode=bool(args.json),
        )
        return 0
    return 0


async def _run_skills(args: argparse.Namespace, client: CycberApiClient) -> int:
    if args.skills_command == "sources":
        if args.sources_command == "list":
            print_payload(await client.skill_repositories())
            return 0
        if args.sources_command == "refresh":
            print_payload(await client.refresh_skill_repository(args.repository_id))
            return 0
        if args.sources_command == "add":
            print_payload(
                await client.upsert_skill_repository(
                    args.repository_id,
                    {
                        "display_name": args.display_name,
                        "provider": args.provider,
                        "index_uri": args.index_uri,
                        "priority": args.priority,
                        "is_default": bool(args.default),
                        "status": "enabled",
                    },
                )
            )
            return 0
        if args.sources_command == "disable":
            print_payload(await client.disable_skill_repository(args.repository_id))
            return 0
    if args.skills_command == "search":
        print_payload(
            await client.search_skills(
                args.query,
                repository_id=args.source,
                limit=args.limit,
            ),
            json_mode=bool(args.json),
        )
        return 0
    if args.skills_command == "install":
        payload = _skill_install_payload(args)
        if args.preview:
            print_payload(await client.preview_skill_install(payload), json_mode=bool(args.json))
            return 0
        result = await client.install_skill(payload)
        if args.enable:
            bundle_id = result.get("bundle", {}).get("bundle_id")
            if bundle_id:
                result["enabled"] = await client.enable_plugin(str(bundle_id))
        if args.grant_default:
            result["grants"] = await _grant_default_installed_skills(result, client)
        print_payload(result, json_mode=bool(args.json))
        return 0
    if args.skills_command == "grant":
        payload = {
            "subject_type": "member",
            "subject_id": args.member,
            "allowed_tools": list(args.tools),
            "grant_scope": "explicit",
            "created_by_member_id": args.member,
        }
        print_payload(
            await client.grant_skill(args.skill_id, payload),
            json_mode=bool(args.json),
        )
        return 0
    return 0


def _task_skill_input(args: argparse.Namespace) -> dict[str, Any]:
    skill_input: dict[str, Any] = {}
    if args.input_json:
        raw = Path(args.input_json)
        text = raw.read_text(encoding="utf-8") if raw.exists() else args.input_json
        import json

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("--input-json must be a JSON object or a path to one")
        skill_input.update(parsed)
    skill_input.setdefault("goal", args.goal)
    if args.content:
        skill_input.setdefault("content", args.content)
    return skill_input


def _export_artifacts(items: list[dict[str, Any]], export_dir: Path | None) -> list[dict[str, Any]]:
    if export_dir is None:
        return []
    root = find_repo_root()
    artifact_root = (root / "data" / "artifacts").resolve()
    target_dir = export_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, Any]] = []
    for item in items:
        display_name = str(item.get("display_name") or "")
        if Path(display_name).suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
            continue
        source = _artifact_path(artifact_root, item)
        target = _unique_target(target_dir / Path(display_name).name)
        shutil.copy2(source, target)
        exported.append(
            {
                "artifact_id": item.get("artifact_id"),
                "display_name": display_name,
                "path": str(target),
            }
        )
    return exported


def _artifact_path(artifact_root: Path, item: dict[str, Any]) -> Path:
    uri = str(item.get("uri") or "")
    if not uri.startswith("artifact://"):
        raise ValueError(f"Unsupported artifact URI: {uri}")
    relative = Path(uri.removeprefix("artifact://"))
    source = (artifact_root / relative).resolve()
    if artifact_root not in [source, *source.parents]:
        raise ValueError(f"Artifact path escaped root: {uri}")
    if not source.exists():
        raise FileNotFoundError(str(source))
    return source


def _unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(str(path))


async def _grant_default_installed_skills(
    result: dict[str, Any],
    client: CycberApiClient,
) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    for skill in result.get("skills") or []:
        skill_id = str(skill.get("skill_id") or "")
        tools = [str(item) for item in skill.get("required_tools") or [] if str(item)]
        if not skill_id or not tools or not _low_risk_default_grant_tools(tools):
            continue
        grants.append(
            await client.grant_skill(
                skill_id,
                {
                    "subject_type": "member",
                    "subject_id": "mem_xiaoyao",
                    "allowed_tools": tools,
                    "grant_scope": "curated_default",
                    "created_by_member_id": "mem_xiaoyao",
                },
            )
        )
    return grants


def _low_risk_default_grant_tools(tools: list[str]) -> bool:
    return all(
        tool.startswith("office.")
        or tool in {"file.write", "file.read", "file.list", "file.hash"}
        for tool in tools
    )


def _skill_install_payload(args: argparse.Namespace) -> dict[str, Any]:
    source_type = args.source_type or _infer_skill_source_type(args.ref, args.source)
    install_options: dict[str, Any] = {}
    if args.checksum:
        install_options["checksum"] = args.checksum
    if args.bundle_id:
        install_options["bundle_id"] = args.bundle_id
    payload: dict[str, Any] = {
        "source_type": source_type,
        "source_uri": args.ref,
        "requested_by_member_id": "mem_xiaoyao",
        "install_options": install_options,
    }
    if args.source:
        payload["repository_id"] = args.source
    return payload


def _infer_skill_source_type(ref: str, source: str | None) -> str:
    lowered = ref.lower()
    if source or (":" in ref and not lowered.startswith(("http://", "https://"))):
        return "repository_ref"
    if lowered.startswith("https://github.com/") or lowered.startswith("github:"):
        return "github_path"
    if lowered.startswith(("https://", "http://")) and lowered.endswith(".md"):
        return "skill_md_url"
    if lowered.startswith(("https://", "http://")):
        return "archive_url"
    if lowered.endswith((".zip", ".tar", ".tar.gz", ".tgz")):
        return "local_archive"
    return "local_directory"


async def _run_chat(args: argparse.Namespace, client: CycberApiClient, state: CliState) -> int:
    message = getattr(args, "message", None)
    json_mode = bool(getattr(args, "json", False))
    if getattr(args, "interactive", False) or not message:
        return await run_repl(client, state, json_mode=json_mode)
    result = await send_message(
        client,
        state,
        message,
        conversation_id=getattr(args, "conversation", None),
        member_id=getattr(args, "member", None),
        session_id=getattr(args, "session", None),
        stream=bool(getattr(args, "stream", True)),
        include_diagnostics=bool(getattr(args, "diagnostics", False)),
    )
    state.save()
    exported = await _export_chat_artifacts(
        result.artifacts,
        getattr(args, "export_dir", None),
        client,
    )
    payload: dict[str, Any] = {
        "turn": result.created,
        "text": result.text,
        "artifacts": result.artifacts,
        "exported": exported,
        "diagnostics": result.diagnostics,
    }
    verbose_payload = json_mode or bool(getattr(args, "diagnostics", False))
    visible = _format_chat_output(result.text, result.artifacts, exported)
    print_payload(payload if verbose_payload else visible, json_mode=json_mode)
    return 0


async def _export_chat_artifacts(
    artifacts: list[dict[str, Any]],
    export_dir: Path | None,
    client: CycberApiClient,
) -> list[dict[str, Any]]:
    if export_dir is None:
        return []
    target_dir = export_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, Any]] = []
    for item in artifacts:
        artifact_id = str(item.get("artifact_id") or "").strip()
        if not artifact_id:
            continue
        content, _headers = await client.download_artifact(artifact_id)
        display_name = _safe_output_name(str(item.get("display_name") or artifact_id))
        target = _unique_target(target_dir / display_name)
        target.write_bytes(content)
        exported.append(
            {
                "artifact_id": artifact_id,
                "display_name": display_name,
                "path": str(target),
            }
        )
    return exported


def _format_chat_output(
    text: str,
    artifacts: list[dict[str, Any]],
    exported: list[dict[str, Any]],
) -> str:
    if not artifacts:
        return text
    lines = [text.rstrip(), "", "文件："]
    exported_by_id = {
        str(item.get("artifact_id")): item for item in exported if item.get("artifact_id")
    }
    for item in artifacts:
        artifact_id = str(item.get("artifact_id") or "")
        name = str(item.get("display_name") or artifact_id or "artifact")
        content_type = str(item.get("content_type") or "application/octet-stream")
        download_url = str(item.get("download_url") or "")
        line = f"- {name} ({content_type})"
        if download_url:
            line += f" {download_url}"
        exported_item = exported_by_id.get(artifact_id)
        if exported_item:
            line += f" -> {exported_item.get('path')}"
        lines.append(line)
    return "\n".join(lines)


def _safe_output_name(value: str) -> str:
    name = Path(value).name.strip().replace("\\", "_").replace("/", "_")
    return name or "artifact.bin"


async def _run_conversations(
    args: argparse.Namespace,
    client: CycberApiClient,
    state: CliState,
) -> int:
    if args.conversation_command == "list":
        print_payload({"items": await client.conversations()})
    elif args.conversation_command == "show":
        print_payload(await client.conversation(args.conversation_id))
    elif args.conversation_command == "use":
        conversation = await client.conversation(args.conversation_id)
        state.conversation_id = args.conversation_id
        state.member_id = conversation.get("primary_member_id") or state.member_id
        state.save()
        print_payload({"conversation_id": state.conversation_id, "member_id": state.member_id})
    return 0


async def _run_turns(args: argparse.Namespace, client: CycberApiClient) -> int:
    if args.turn_command == "show":
        print_payload(compact_turn(await client.turn(args.turn_id)))
    elif args.turn_command in {"events", "replay"}:
        print_payload({"items": await client.turn_events(args.turn_id)})
    elif args.turn_command == "brain":
        print_payload(await client.brain_decision(args.turn_id))
    elif args.turn_command == "semantic":
        print_payload(await client.semantic_review(args.turn_id))
    elif args.turn_command == "quality":
        print_payload(
            {
                "tone_policy": await client.tone_policy(args.turn_id),
                "response_quality": await client.response_quality(args.turn_id),
            }
        )
    return 0


async def _optional(call: Any) -> Any:
    try:
        return await call()
    except ApiError as exc:
        return {"status": "not_available", "error": exc.payload}


if __name__ == "__main__":
    sys.exit(main())
