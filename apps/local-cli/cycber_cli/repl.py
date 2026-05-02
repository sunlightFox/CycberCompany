from __future__ import annotations

from cycber_cli.chat import send_message
from cycber_cli.http_client import CycberApiClient
from cycber_cli.output import print_payload
from cycber_cli.state import CliState

HELP = (
    "本地命令：/help /status /doctor /conversations /use <id> "
    "/events last /brain last /quality last /skills search <q> "
    "/skills install <ref> /skills grant <skill_id> <tool...> /exit"
)


async def run_repl(client: CycberApiClient, state: CliState, *, json_mode: bool = False) -> int:
    print("小曜 > 已进入 CLI 聊天。输入 /help 查看本地命令，/exit 退出。")
    while True:
        try:
            user_input = input("你  > ").strip()
        except EOFError:
            return 0
        if not user_input:
            continue
        if user_input.startswith("/"):
            should_exit = await _handle_slash(client, state, user_input, json_mode=json_mode)
            if should_exit:
                return 0
            continue
        result = await send_message(client, state, user_input)
        print_payload(result.text or result.created, json_mode=json_mode)
        state.save()


async def _handle_slash(
    client: CycberApiClient,
    state: CliState,
    command: str,
    *,
    json_mode: bool,
) -> bool:
    parts = command.split()
    name = parts[0].lower()
    if name in {"/exit", "/quit"}:
        return True
    if name == "/help":
        print(HELP)
    elif name == "/status":
        print_payload(
            {"state": state.__dict__, "health": await client.health()},
            json_mode=json_mode,
        )
    elif name == "/doctor":
        print_payload(
            {"health": await client.health(), "full": await client.full_health()},
            json_mode=json_mode,
        )
    elif name == "/conversations":
        print_payload({"items": await client.conversations()}, json_mode=json_mode)
    elif name == "/use" and len(parts) >= 2:
        state.conversation_id = parts[1]
        conversation = await client.conversation(parts[1])
        state.member_id = conversation.get("primary_member_id") or state.member_id
        state.save()
        print_payload({"conversation_id": state.conversation_id}, json_mode=json_mode)
    elif name in {"/events", "/replay"}:
        turn_id = _turn_arg(parts, state)
        print_payload({"items": await client.turn_events(turn_id)}, json_mode=json_mode)
    elif name == "/brain":
        print_payload(await client.brain_decision(_turn_arg(parts, state)), json_mode=json_mode)
    elif name in {"/semantic"}:
        print_payload(await client.semantic_review(_turn_arg(parts, state)), json_mode=json_mode)
    elif name in {"/quality"}:
        turn_id = _turn_arg(parts, state)
        print_payload(
            {
                "tone_policy": await client.tone_policy(turn_id),
                "response_quality": await client.response_quality(turn_id),
            },
            json_mode=json_mode,
        )
    elif name == "/trace":
        trace_id = parts[1] if len(parts) > 1 and parts[1] != "last" else state.last_trace_id
        if trace_id:
            print_payload(await client.trace(trace_id), json_mode=json_mode)
    elif name == "/skills" and len(parts) >= 3 and parts[1] == "search":
        print_payload(
            await client.search_skills(" ".join(parts[2:])),
            json_mode=json_mode,
        )
    elif name == "/skills" and len(parts) >= 3 and parts[1] == "install":
        ref = parts[2]
        source_type = "repository_ref" if ":" in ref else "local_directory"
        print_payload(
            await client.install_skill(
                {
                    "source_type": source_type,
                    "source_uri": ref,
                    "requested_by_member_id": state.member_id,
                }
            ),
            json_mode=json_mode,
        )
    elif name == "/skills" and len(parts) >= 4 and parts[1] == "grant":
        skill_id = parts[2]
        tools = parts[3:]
        print_payload(
            await client.grant_skill(
                skill_id,
                {
                    "subject_type": "member",
                    "subject_id": state.member_id,
                    "allowed_tools": tools,
                    "grant_scope": "explicit",
                    "created_by_member_id": state.member_id,
                },
            ),
            json_mode=json_mode,
        )
    elif name == "/clear":
        print("\033c", end="")
    else:
        print(HELP)
    return False


def _turn_arg(parts: list[str], state: CliState) -> str:
    turn_id = parts[1] if len(parts) > 1 and parts[1] != "last" else state.last_turn_id
    if not turn_id:
        raise RuntimeError("没有 last turn；请传入 turn_id。")
    return turn_id
