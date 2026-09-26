"""
sse.py — formatting Server-Sent Events.

An SSE stream is a long-lived HTTP response in a very simple text format:
each event is a line beginning `data: `, followed by a BLANK line. The blank
line is what tells the browser one event has ended and the next begins.

    data: {"type": "text", "text": "Hello"}
    <blank>
    data: {"type": "text", "text": " there"}
    <blank>

That is the whole protocol. It is also exactly the kind of thing that breaks
in ways nobody notices for a week — one newline instead of two and the
browser waits forever for an event that has already been sent. So it lives
here, written once, with tests of its own.
"""

import json

# What the response must say for a browser to treat it as a stream.
HEADERS = {
    "Cache-Control": "no-cache",
    # Some proxies hold a response until it is complete, which quietly turns
    # streaming back into waiting. This asks them not to. It changes nothing
    # locally, which is exactly why it gets discovered in production.
    "X-Accel-Buffering": "no",
}

MEDIA_TYPE = "text/event-stream"


def event(payload: dict) -> str:
    """One event, ready to send.

    `ensure_ascii=False` keeps accented characters and symbols as themselves
    rather than as escape sequences — shorter, and readable when you are
    watching the stream in a terminal.
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def error_event(message: str) -> str:
    """A failure the client has to render itself.

    Once a stream has started, the response has already promised 200 OK and
    the status code can no longer say anything. Errors after that point
    travel inside the stream, as data.
    """
    return event({"type": "error", "message": message})
