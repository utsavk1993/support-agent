"""
main.py — the web server.

Two jobs:

  1. Hand the browser the chat page                ->  GET  /
  2. Take a message, ask the model, send the reply ->  POST /api/chat

If you've used Express in Node, this will feel familiar: routes, a handler
per route, JSON in and JSON out. FastAPI adds automatic checking of the
incoming data, which we use below.

RUN IT WITH:
    cd support_agent
    ../.venv/bin/uvicorn main:app --reload

Then open http://127.0.0.1:8000
"""

import json
from pathlib import Path  # Works out where index.html lives, no matter what folder you run the command from.

from fastapi import FastAPI, HTTPException  # The web server itself, plus the way we send back an error.
from fastapi.responses import (  # Hands back a file from disk, or a reply sent in pieces.
    FileResponse,
    StreamingResponse,
)
from pydantic import BaseModel, Field  # Describes the shape of incoming JSON so FastAPI can check it for us.

import llm  # Our own file — the only thing here that talks to the model.
from prompts import SUPPORT_POLICY  # The rulebook the agent has to follow.

# Create the app. `title` is only used on FastAPI's auto-generated docs page,
# which you get for free at http://127.0.0.1:8000/docs — worth a look.
app = FastAPI(title="Northwind Support Agent")

# Where index.html lives. We work this out from THIS file's location rather
# than assuming where you ran the command from, so it works no matter what
# folder your terminal is sitting in.
STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# THE SHAPE OF THE DATA COMING IN
# ---------------------------------------------------------------------------
# These classes describe what a valid request looks like. FastAPI checks every
# incoming request against them automatically. If the browser sends something
# wrong, it gets a clear error back and our code below never even runs —
# so we don't have to write "if not body.get('messages')" defensive checks.
#
# Think of it as TypeScript types, except they're enforced at runtime.

class Message(BaseModel):
    """One line of the conversation."""
    # Who said it: "user" (the customer) or "assistant" (our agent).
    role: str
    # What they said.
    content: str


class ChatRequest(BaseModel):
    """What the browser POSTs to us."""
    # The WHOLE conversation so far, oldest first — not just the new message.
    #
    # This looks wasteful and it is, a bit. But the model has no memory at
    # all. It never remembers anything between calls. The only reason a chat
    # feels like a conversation is that we resend the entire history every
    # single time, and the model re-reads it from scratch.
    #
    # The browser is what holds that history, which is why refreshing the
    # page loses the conversation.
    #
    # min_length=1 means "reject an empty conversation" — FastAPI enforces it.
    messages: list[Message] = Field(min_length=1)


# ---------------------------------------------------------------------------
# ROUTE 1: hand over the web page
# ---------------------------------------------------------------------------
# The @ line is a "decorator". It attaches the function below it to a URL.
# Same idea as app.get("/", handler) in Express.
@app.get("/")
async def serve_page():
    """When someone visits the site, send them the chat page."""
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# ROUTE 2: the actual chat
# ---------------------------------------------------------------------------
@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Take the conversation, ask the model, and stream the reply back.

    Rather than waiting for the whole answer and sending it in one go, we
    forward each piece the moment it arrives. The user sees words appear
    instead of staring at a placeholder.

    `async def` matters here. A plain `def` handler gets one thread from a
    pool of 40 and holds it for the whole request, so the 41st person to
    arrive at once waits for someone else to finish. An `async def` handler
    holds no thread while it waits, so thousands can be in flight.

    That matters more now than before: a streamed request keeps its
    connection open for the whole reply, seconds rather than milliseconds.
    """
    # Build what we send. The policy goes FIRST, as a "system" message:
    # it is instructions rather than conversation, and it never changes, so
    # keeping it at the front lets the provider recognise a repeated prefix
    # and charge less for it.
    model_messages = [{"role": "system", "content": SUPPORT_POLICY}]
    for message in request.messages:
        model_messages.append({"role": message.role, "content": message.content})

    pieces = llm.stream(model=llm.MODEL, messages=model_messages, max_tokens=1024)

    # Pull the FIRST piece before we answer the browser at all.
    #
    # This is the whole trick for keeping error handling sane. Once we start
    # streaming, the browser has already been told "200 OK" and we can no
    # longer change our mind about the status code. By fetching one piece
    # up front, a provider failure still becomes a proper 502 — exactly as
    # it did before streaming.
    #
    # `anext` runs the generator up to its first yield, which is where the
    # request is actually made.
    try:
        first_piece = await anext(pieces, None)
    except Exception as error:
        # Nothing has been sent yet, so we can still fail properly.
        # The real error goes to our logs; the customer gets something safe,
        # since provider errors can name internal hosts and services.
        print(f"[chat failed] {type(error).__name__}: {error}")
        raise HTTPException(
            status_code=502,  # "the thing I depend on is broken"
            detail="The assistant is unavailable right now. Please try again.",
        ) from error

    async def send_events():
        """Yield the reply as Server-Sent Events.

        SSE is just a long-lived response with a simple text format: each
        event is a line starting `data: `, followed by a blank line. We put
        one JSON object on each line, so the browser can tell the difference
        between text, token counts and errors.
        """
        try:
            if first_piece is not None:
                yield f"data: {json.dumps(first_piece)}\n\n"

            async for piece in pieces:
                yield f"data: {json.dumps(piece)}\n\n"

        except Exception as error:
            # We are past the point of no return: the browser already has
            # part of the answer and a 200 status. The only way to report
            # this is INSIDE the stream, and the client has to handle it.
            print(f"[stream broke] {type(error).__name__}: {error}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'The reply was cut short. Please try again.'})}\n\n"

    return StreamingResponse(
        send_events(),
        media_type="text/event-stream",
        headers={
            # Some proxies hold a response until it is complete, which turns
            # streaming back into waiting. This asks them not to. It changes
            # nothing locally, which is exactly why it is easy to forget
            # until it breaks in production.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
