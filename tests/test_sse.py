"""Tests for the Server-Sent Events format.

Small, exact, and easy to get wrong in ways that fail silently — a missing
newline means the browser waits forever for an event already sent.
"""

import json

from app import sse


def test_an_event_ends_with_a_blank_line():
    """The blank line is what separates one event from the next."""
    assert sse.event({"type": "text", "text": "hi"}).endswith("\n\n")


def test_an_event_starts_with_the_data_prefix():
    assert sse.event({"type": "text"}).startswith("data: ")


def test_the_payload_survives_a_round_trip():
    payload = {"type": "usage", "prompt_tokens": 684, "thinking_tokens": 40}
    raw = sse.event(payload)
    assert json.loads(raw.removeprefix("data: ").rstrip()) == payload


def test_two_events_split_cleanly():
    """How the browser separates them: split on the blank line."""
    stream = sse.event({"type": "text", "text": "a"}) + sse.event({"type": "text", "text": "b"})
    blocks = [b for b in stream.split("\n\n") if b]
    assert len(blocks) == 2


def test_non_ascii_is_sent_as_itself():
    """Not as \\u escapes — shorter, and readable in a terminal."""
    assert "°C" in sse.event({"type": "text", "text": "34°C"})


def test_an_error_event_carries_its_message():
    payload = json.loads(sse.error_event("cut short").removeprefix("data: "))
    assert payload == {"type": "error", "message": "cut short"}
