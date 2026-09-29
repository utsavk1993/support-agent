"""
routes.py — the HTTP endpoints.

  GET  /                        the chat page
  POST /api/chat                send a message, stream the reply
  GET  /api/conversations/{id}  load a stored conversation

Separate from main.py, which builds the application. These two change for
different reasons: this file changes when the product does, that one when
the infrastructure does.
"""

import json
import uuid

from fastapi import APIRouter, HTTPException, Request  # Route grouping, errors, the incoming request.
from fastapi.responses import FileResponse, StreamingResponse  # A file from disk, or a reply sent in pieces.
from pydantic import BaseModel, Field  # Describes the shape of incoming JSON so FastAPI can check it for us.

from app import config, llm, sse, store, tools  # Our own modules.
from app.prompts import SUPPORT_POLICY, VERIFICATION_ONLY  # What the agent is told.

# How many times the model may call tools before we stop it. A model that
# misreads a result can ask forever; the cap turns an unbounded spend into
# a bounded failure.
MAX_TOOL_ROUNDS = 6

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

    # WHICH PROMPT, AND WHICH TOOLS, DEPEND ON WHETHER THEY HAVE VERIFIED.
    #
    # An unverified conversation is not sent the support policy at all, and
    # is not offered the tools that read customer data. It cannot disclose
    # what it was never given, and cannot call what it was never offered.
    #
    # Telling the model "refuse until they verify" would be an instruction,
    # and instructions are exactly what prompt injection argues with. This
    # is not an instruction; it is an absence.
    customer = await store.verified_customer(conversation_id)

    system = SUPPORT_POLICY if customer else VERIFICATION_ONLY
    if customer:
        system += f"\n\nYou are speaking to {customer['name']}, who has verified their identity."

    # Load the conversation from the database rather than trusting the
    # browser to send it.
    history = await store.load_messages(conversation_id, owner)
    conversation = [{"role": "system", "content": system}] + history

    async def send_events():
        """Run the model, and its tools, until it has an answer.

        One customer message can take several trips to the model: it asks
        for a tool, we run it, it reads the result and either asks for
        another or writes the reply. All of that happens inside this one
        response, while the customer watches.
        """
        reply = ""
        usage = None

        yield sse.event({"type": "conversation", "id": conversation_id})

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                # Whether the conversation is verified can change mid-turn,
                # the moment submit_verification_code succeeds — so the
                # tools on offer are worked out fresh each round.
                now_verified = await store.verified_customer(conversation_id) is not None
                requested = []

                async for piece in llm.stream(
                    model=llm.MODEL,
                    messages=conversation,
                    max_tokens=config.MAX_REPLY_TOKENS,
                    tools=tools.tools_for(now_verified),
                ):
                    if piece["type"] == "tool_calls":
                        requested = piece["calls"]
                        continue  # not for the browser
                    if piece["type"] == "text":
                        reply += piece["text"]
                    elif piece["type"] == "usage":
                        usage = piece
                    yield sse.event(piece)

                if not requested:
                    break  # it answered

                # Record what it asked for, so the next trip can see it.
                conversation.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(call["arguments"]),
                                },
                            }
                            for call in requested
                        ],
                    }
                )

                for call in requested:
                    # Tell the browser what is happening. Without this the
                    # customer watches a pause and cannot tell the
                    # difference between working and broken.
                    yield sse.event({"type": "tool", "name": call["name"], "arguments": call["arguments"]})

                    try:
                        result = await tools.run(call["name"], call["arguments"], conversation_id, now_verified)
                    except Exception as error:
                        # A tool that fails is data the model can work with,
                        # not a reason to abandon the turn. The detail stays
                        # in our logs.
                        print(f"[tool failed] {call['name']}: {type(error).__name__}: {error}")
                        result = {"error": "That did not work. Tell the customer and offer to try again."}

                    # A return needs a person to agree to it. The client
                    # draws a button; pressing it is a separate request that
                    # checks everything again.
                    if result.get("awaiting_confirmation"):
                        yield sse.event({"type": "confirm", **result})

                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            # WHATEVER IS IN HERE IS DATA, NOT INSTRUCTIONS.
                            # It contains text customers typed — return
                            # reasons, order notes. The prompt says so, and
                            # the tools are scoped so that believing it
                            # would not grant access to anything.
                            "content": json.dumps(result),
                        }
                    )
            else:
                yield sse.error_event("This is taking more steps than expected. Please try asking again.")

        except Exception as error:
            print(f"[stream broke] {type(error).__name__}: {error}")
            yield sse.error_event("The reply was cut short. Please try again.")

        finally:
            if reply:
                await store.add_message(conversation_id, "assistant", reply, usage)

    return StreamingResponse(
        send_events(),
        media_type=sse.MEDIA_TYPE,
        headers=sse.HEADERS,
    )


class ConfirmReturn(BaseModel):
    """What the client sends when the customer presses Confirm."""

    conversation_id: str
    order_number: str = Field(min_length=1, max_length=40)
    item_id: int
    reason: str = Field(default="", max_length=500)


@router.post("/api/returns")
async def confirm_return(body: ConfirmReturn, request: Request):
    """Actually open a return, because a person asked for it.

    This is the other half of the approval gate, and the reason it is a
    separate request rather than another tool.

    The model can propose a return. It cannot open one — there is no tool
    that does. Opening happens here, reached only by someone pressing a
    button in their own browser, and everything is checked again from
    scratch: the conversation belongs to this session, the session is
    verified, and the order belongs to that customer.

    If the model could confirm its own proposal, an instruction hidden in
    a tool result could supply the agreement. A button cannot be pressed
    by a sentence.
    """
    owner = owner_of(request)
    parse_conversation_id(body.conversation_id)

    if not await store.conversation_belongs_to(body.conversation_id, owner):
        raise NOT_FOUND

    if await store.verified_customer(body.conversation_id) is None:
        raise NOT_FOUND

    await store.record_access(body.conversation_id, "start_return", body.order_number)
    opened = await store.open_return(body.conversation_id, body.order_number, body.item_id, body.reason)
    if opened is None:
        # The order is not theirs, or the item is not on it. Same answer
        # either way.
        raise NOT_FOUND

    # Record it in the conversation, so a reload shows what happened
    # rather than a proposal with no outcome.
    await store.add_message(
        body.conversation_id,
        "assistant",
        f"Return opened for {opened['order_number']} — reference {opened['id'][:8]}.",
    )

    return {
        "return_id": opened["id"],
        "order_number": opened["order_number"],
        "status": opened["status"],
    }
