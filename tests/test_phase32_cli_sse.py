from __future__ import annotations

from cycber_cli.sse import SSEDecoder, parse_sse_lines


def test_phase32_sse_parser_handles_named_and_multiline_events() -> None:
    events = parse_sse_lines(
        [
            "event: response.delta",
            "id: evt_1",
            "data: {\"payload\":{\"text\":\"hello\"}}",
            "",
            "event: response.delta",
            "data: line one",
            "data: line two",
            "",
        ]
    )

    assert len(events) == 2
    assert events[0].event == "response.delta"
    assert events[0].event_id == "evt_1"
    assert events[0].data == "{\"payload\":{\"text\":\"hello\"}}"
    assert events[1].data == "line one\nline two"


def test_phase32_sse_decoder_flushes_last_event_on_close() -> None:
    decoder = SSEDecoder()
    assert decoder.feed_line("event: turn.completed") == []
    assert decoder.feed_line("data: {\"status\":\"completed\"}") == []

    events = decoder.close()

    assert len(events) == 1
    assert events[0].event == "turn.completed"
