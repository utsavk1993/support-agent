"""
routes.py — the HTTP endpoints.

  GET  /                        the chat page
  POST /api/chat                send a message, stream the reply
  GET  /api/conversations/{id}  load a stored conversation

Separate from main.py, which builds the application. These two change for
different reasons: this file changes when the product does, that one when
the infrastructure does.
"""

import uuid

from fastapi import APIRouter, HTTPException, Request  # Route grouping, errors, the incoming request.
from fastapi.responses import FileResponse, StreamingResponse  # A file from disk, or a reply sent in pieces.
from pydantic import BaseModel, Field  # Describes the shape of incoming JSON so FastAPI can check it for us.

from app import config, llm, sse, store  # Our own modules.
from app.prompts import SUPPORT_POLICY  # The rulebook the agent has to follow.

# A router collects endpoints so main.py can attach them in one line, rather
# than every route needing to reach for the application object itself.
router = APIRouter()

# The answer to every request for a conversation the caller cannot have,
# whatever the reason: it does not exist, it belongs to someone else, or the
# id is not even a real id.
#
# 404 rather than 403, and deliberately so. A 403 confirms the conversation
# exists, which lets someone probe for valid ids. Returning one answer for
# all three cases means the response reveals nothing about which applies.
NOT_FOUND = HTTPException(status_code=404, detail="Not found.")


def parse_conversation_id(value: str) -> uuid.UUID:
    """Turn a conversation id from a URL into a UUID, or refuse it.

    Conversation ids are uuid columns in the database. A string that is not
    a UUID cannot match anything, and handed to the driver it raises rather
    than returning no rows — which surfaced as a 500 and a stack trace for
    anyone who mistyped a URL.

    It has to be refused the SAME way an unknown id is. A malformed id
    returning 500 while an unknown one returns 404 would tell someone
    probing which of their guesses were at least shaped correctly, which is
    the signal 404 exists to withhold.
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise NOT_FOUND from None


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


@router.get("/")
async def serve_page():
    """When someone visits the site, send them the chat page.

    The page is built from the React app in web/ and is not committed, so a
    fresh checkout has none until it has been built. Saying so plainly beats
    a 500 that makes it look like the server is broken.
    """
    page = config.STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(
            status_code=503,
            detail="The client has not been built. Run: npm --prefix web install && npm --prefix web run build",
        )
    return FileResponse(page)


@router.get("/api/conversations/{conversation_id}")
async def load_conversation(conversation_id: str, request: Request):
    """The stored messages of a conversation, so a refreshed page can restore it."""
    owner = owner_of(request)
    parse_conversation_id(conversation_id)

    if not await store.conversation_belongs_to(conversation_id, owner):
        raise NOT_FOUND

    return {
        "conversation_id": conversation_id,
        # load_transcript, not load_messages: the browser wants token
        # counts, which the model must never be sent.
        "messages": await store.load_transcript(conversation_id, owner),
    }


@router.post("/api/chat")
async def chat(body: ChatRequest, request: Request):
    """Take one message, ask the model, and stream the reply back.

    Rather than waiting for the whole answer and sending it in one go, we
    forward each piece the moment it arrives, so the user sees words appear
    instead of staring at a placeholder.
    """
    owner = owner_of(request)

    # Continue an existing conversation, or start a new one.
    #
    # `is not None` rather than a plain truth test, so an empty string is
    # treated as a malformed id rather than as "no id given". A client that
    # sends "" has a bug, and quietly starting a new conversation hides it —
    # the customer would simply lose their history with no error anywhere.
    if body.conversation_id is not None:
        parse_conversation_id(body.conversation_id)
        if not await store.conversation_belongs_to(body.conversation_id, owner):
            raise NOT_FOUND
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

    pieces = llm.stream(model=llm.MODEL, messages=model_messages, max_tokens=config.MAX_REPLY_TOKENS)

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
        yield sse.event({"type": "conversation", "id": conversation_id})

        try:
            for piece in (first_piece,):
                if piece is None:
                    continue
                if piece["type"] == "text":
                    reply += piece["text"]
                yield sse.event(piece)

            async for piece in pieces:
                if piece["type"] == "text":
                    reply += piece["text"]
                elif piece["type"] == "usage":
                    usage = piece
                yield sse.event(piece)

        except Exception as error:
            # Past the point of no return: the browser already has part of
            # the answer and a 200 status. The only way to report this is
            # inside the stream, and the client has to handle it.
            print(f"[stream broke] {type(error).__name__}: {error}")
            yield sse.error_event("The reply was cut short. Please try again.")

        finally:
            # Save whatever the customer actually saw, even if the stream
            # died halfway. They read that text; a reload should show it.
            # Nothing to save only if the reply never started.
            if reply:
                await store.add_message(conversation_id, "assistant", reply, usage)

    return StreamingResponse(
        send_events(),
        media_type=sse.MEDIA_TYPE,
        headers=sse.HEADERS,
    )
