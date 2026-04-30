from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class SSEEvent:
    event: str = "message"
    data: str = ""
    event_id: str | None = None
    retry: int | None = None


class SSEDecoder:
    def __init__(self) -> None:
        self._event = "message"
        self._data: list[str] = []
        self._event_id: str | None = None
        self._retry: int | None = None

    def feed_line(self, line: str) -> list[SSEEvent]:
        if line == "":
            event = self._dispatch()
            return [event] if event is not None else []
        if line.startswith(":"):
            return []
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value or "message"
        elif field == "data":
            self._data.append(value)
        elif field == "id":
            self._event_id = value
        elif field == "retry":
            try:
                self._retry = int(value)
            except ValueError:
                self._retry = None
        return []

    def close(self) -> list[SSEEvent]:
        event = self._dispatch()
        return [event] if event is not None else []

    def _dispatch(self) -> SSEEvent | None:
        if not self._data and self._event == "message" and self._event_id is None:
            return None
        event = SSEEvent(
            event=self._event,
            data="\n".join(self._data),
            event_id=self._event_id,
            retry=self._retry,
        )
        self._event = "message"
        self._data = []
        self._event_id = None
        self._retry = None
        return event


def parse_sse_lines(lines: Iterable[str]) -> list[SSEEvent]:
    decoder = SSEDecoder()
    events: list[SSEEvent] = []
    for line in lines:
        events.extend(decoder.feed_line(line.rstrip("\r\n")))
    events.extend(decoder.close())
    return events
