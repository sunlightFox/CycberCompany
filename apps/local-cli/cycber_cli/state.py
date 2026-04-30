from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cycber_cli.config import DEFAULT_BASE_URL
from cycber_cli.redaction import redact


def cli_home() -> Path:
    configured = os.environ.get("CYCBER_CLI_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cycbercompany" / "cli"


@dataclass
class CliState:
    base_url: str = DEFAULT_BASE_URL
    conversation_id: str | None = None
    member_id: str | None = None
    session_id: str | None = None
    last_turn_id: str | None = None
    last_trace_id: str | None = None
    output_mode: str = "human"
    autostart: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> CliState:
        state_path = path or default_state_path()
        if not state_path.exists():
            return cls()
        data = json.loads(state_path.read_text(encoding="utf-8"))
        allowed = {field for field in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def save(self, path: Path | None = None) -> None:
        state_path = path or default_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = redact(asdict(self))
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_from_conversation(self, item: dict[str, Any]) -> None:
        self.conversation_id = str(item.get("conversation_id") or self.conversation_id or "")
        self.member_id = str(item.get("primary_member_id") or self.member_id or "")


def default_state_path() -> Path:
    return cli_home() / "state.json"
