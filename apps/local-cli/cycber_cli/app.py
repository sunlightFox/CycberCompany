from __future__ import annotations

import argparse
import asyncio
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
    chat.add_argument("--autostart", dest="autostart", action="store_true", default=True)
    chat.add_argument("--no-autostart", dest="autostart", action="store_false")
    chat.add_argument("--timeout", type=int, default=180)


async def _dispatch(args: argparse.Namespace) -> int:
    command = args.command or "chat"
    state = CliState.load()
    if getattr(args, "base_url", None):
        state.base_url = args.base_url
    manager = ServerManager(base_url=state.base_url)
    if command in {"chat", "status", "doctor", "conversations", "turns", "traces"}:
        chat_autostart = command == "chat" and getattr(args, "autostart", True)
        autostart = bool(getattr(args, "autostart", False) or chat_autostart)
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
    payload: dict[str, Any] = {
        "turn": result.created,
        "text": result.text,
        "diagnostics": result.diagnostics,
    }
    verbose_payload = json_mode or bool(getattr(args, "diagnostics", False))
    print_payload(payload if verbose_payload else result.text, json_mode=json_mode)
    return 0


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
