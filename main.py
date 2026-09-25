"""
main.py — the web server.

Routes:

  GET  /                        the chat page
  POST /api/chat                send a message, stream the reply
  GET  /api/conversations/{id}  load a stored conversation

If you have used Express in Node, this will feel familiar: routes, a handler
per route, JSON in and JSON out. FastAPI adds automatic checking of the
incoming data, which we use below.

RUN IT WITH:
    docker compose up -d
    uvicorn main:app --reload
"""

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request  # The server, errors, and the incoming request.
from fastapi.responses import (  # Hands back a file from disk, or a reply sent in pieces.
    FileResponse,
    StreamingResponse,
)
from pydantic import BaseModel, Field  # Describes the shape of incoming JSON so FastAPI can check it for us.
from starlette.middleware.sessions import SessionMiddleware  # Signed cookies.

import llm  # Our own file — the only thing here that talks to the model.
import store  # Our own file — the only thing here that talks to the database.
from prompts import SUPPORT_POLICY  # The rulebook the agent has to follow.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the database when the server starts, close it when it stops.

    Everything before `yield` runs at startup, everything after at shutdown.
    The connection pool is opened once and shared, because establishing a
    connection is slow and Postgres limits how many can exist at once.
    """
    await store.connect()
    yield
    await store.disconnect()


app = FastAPI(title="Northwind Support Agent", lifespan=lifespan)

# Gives every visitor a cookie holding a random identifier, signed with our
# secret. The browser can read it but cannot change it: altering the value
# breaks the signature and the server rejects it. That is what makes "this
# conversation is not yours" enforceable rather than merely polite.
#
# The key must stay the same across restarts. Change it and every existing
# session becomes invalid, which on a deploy means logging everyone out.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "development-only-not-for-production"),
    same_site="lax",      # not sent on cross-site requests, which blocks CSRF
    https_only=False,     # set True in production, once there is TLS
)

STATIC_DIR = Path(__file__).parent / "static"


def owner_of(request: Request) -> str:
    """Who is making this request.

    Today a random identifier minted on first visit and kept in the signed
    cookie. Once accounts exist this becomes the logged-in user's id, and
    nothing else in this file has to change — every check below asks "is
    this yours", never "are you logged in".
    """
    if "owner" not in request.session:
        request.session["owner"] = str(uuid.uuid4())
    return request.session["owner"]


class ChatRequest(BaseModel):
    """What the browser POSTs to us.

    Note what is NOT here: the conversation history. The browser used to
    send the whole thing every turn. Now it sends one message and the id of
    the conversation it belongs to, and the server loads the rest.
    """
    message: str = Field(min_length=1, max_length=4000)

    # Absent on the first message of a new conversation.
    conversation_id: str | None = None


@app.get("/")
async def serve_page():
    """When someone visits the site, send them the chat page."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/conversations/{conversation_id}")
async def load_conversation(conversation_id: str, request: Request):
    """The stored messages of a conversation, so a refreshed page can restore it."""
    owner = owner_of(request)

    if not await store.conversation_belongs_to(conversation_id, owner):
        # 404, not 403, and deliberately so. A 403 would confirm that this
        # conversation exists, which lets someone probe for valid ids. A 404
        # is the same refusal while giving nothing away.
        raise HTTPException(status_code=404, detail="Not found.")

    return {
        "conversation_id": conversation_id,
        # load_transcript, not load_messages: the browser wants token
        # counts, which the model must never be sent.
        "messages": await store.load_transcript(conversation_id, owner),
    }


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request):
    """Take one message, ask the model, and stream the reply back.

    Rather than waiting for the whole answer and sending it in one go, we
    forward each piece the moment it arrives, so the user sees words appear
    instead of staring at a placeholder.
    """
    owner = owner_of(request)

    # Continue an existing conversation, or start a new one.
    if body.conversation_id:
        if not await store.conversation_belongs_to(body.conversation_id, owner):
            raise HTTPException(status_code=404, detail="Not found.")
        conversation_id = body.conversation_id
    else:
        conversation_id = str(uuid.uuid4())
        await store.create_conversation(conversation_id, owner)

    # Save what the customer said BEFORE calling the model.
    #
    # The order matters. If the model call fails, their message is still
    # recorded and the conversation makes sense on reload. Saving afterwards
    # would lose the question whenever the answer failed.
    await store.add_message(conversation_id, "user", body.message)

    # Load the conversation from the database rather than trusting the
    # browser to send it. The policy goes first: it is instructions rather
    # than conversation, and it never changes, so keeping it at the front
    # lets the provider recognise a repeated prefix and charge less.
    history = await store.load_messages(conversation_id, owner)
    model_messages = [{"role": "system", "content": SUPPORT_POLICY}] + history

    pieces = llm.stream(model=llm.MODEL, messages=model_messages, max_tokens=1024)

    # Pull the FIRST piece before we answer the browser at all.
    #
    # This is the whole trick for keeping error handling sane. Once we start
    # streaming, the browser has already been told "200 OK" and we can no
    # longer change our mind about the status code. By fetching one piece up
    # front, a provider failure still becomes a proper 502.
    try:
        first_piece = await anext(pieces, None)
    except Exception as error:
        # Nothing sent yet, so we can still fail properly. The real error
        # goes to our logs; the customer gets something safe, since provider
        # errors can name internal hosts and services.
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
        reply = ""
        usage = None

        # Tell the browser which conversation this is, so a new one can be
        # remembered and sent back with the next message.
        yield f"data: {json.dumps({'type': 'conversation', 'id': conversation_id})}\n\n"

        try:
            for piece in (first_piece,):
                if piece is None:
                    continue
                if piece["type"] == "text":
                    reply += piece["text"]
                yield f"data: {json.dumps(piece)}\n\n"

            async for piece in pieces:
                if piece["type"] == "text":
                    reply += piece["text"]
                elif piece["type"] == "usage":
                    usage = piece
                yield f"data: {json.dumps(piece)}\n\n"

        except Exception as error:
            # Past the point of no return: the browser already has part of
            # the answer and a 200 status. The only way to report this is
            # inside the stream, and the client has to handle it.
            print(f"[stream broke] {type(error).__name__}: {error}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'The reply was cut short. Please try again.'})}\n\n"

        finally:
            # Save whatever the customer actually saw, even if the stream
            # died halfway. They read that text; a reload should show it.
            # Nothing to save only if the reply never started.
            if reply:
                await store.add_message(conversation_id, "assistant", reply, usage)

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
