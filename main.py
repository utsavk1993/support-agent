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

from pathlib import Path  # Works out where index.html lives, no matter what folder you run the command from.

from fastapi import FastAPI, HTTPException  # The web server itself, plus the way we send back an error.
from fastapi.responses import FileResponse  # Hands the browser a file straight off disk, like index.html.
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
def serve_page():
    """When someone visits the site, send them the chat page."""
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# ROUTE 2: the actual chat
# ---------------------------------------------------------------------------
@app.post("/api/chat")
def chat(request: ChatRequest):
    """Take the conversation, ask the model, hand back its reply.

    That `request: ChatRequest` part is doing real work. FastAPI reads the
    incoming JSON, checks it matches the shape we defined above, and gives
    us a proper Python object. Bad input never reaches this line.
    """

    # Build what we actually send to the model.
    #
    # The policy goes FIRST, as a "system" message. Two reasons:
    #   - It's instructions, not conversation. The model treats it as rules.
    #   - It never changes, so keeping it at the front means the provider
    #     can recognise it as a repeated prefix and charge less for it.
    #
    # Then the conversation so far, exactly as the browser sent it.
    #
    # We rebuild this list fresh on every request and throw it away
    # afterwards. The server holds no conversation state.
    model_messages = [{"role": "system", "content": SUPPORT_POLICY}]
    for message in request.messages:
        model_messages.append({"role": message.role, "content": message.content})

    # Ask the model. llm.chat handles the retrying if the server has a wobble.
    try:
        response = llm.chat(
            model=llm.MODEL,
            messages=model_messages,
            # The longest reply we'll allow. A safety belt: without it, a
            # confused model could ramble for thousands of tokens and bill
            # you for all of them.
            max_tokens=1024,
        )
    except Exception as error:
        # Every retry failed, or something else broke.
        #
        # We deliberately do NOT send the raw error to the browser. It can
        # contain internal details — URLs, keys, server names — that a
        # customer should never see. We log the real thing for ourselves and
        # send back something safe and vague.
        print(f"[chat failed] {type(error).__name__}: {error}")
        raise HTTPException(
            status_code=502,  # "the thing I depend on is broken"
            detail="The assistant is unavailable right now. Please try again.",
        ) from error

    # Pull the reply text out of the response.
    #
    # `choices` is a list because the API can return several alternative
    # answers if you ask for them. We only ever ask for one, so we take [0].
    #
    # `or ""` guards against content being empty — we'd rather send back an
    # empty string than the word "None" appearing in the chat window.
    reply_text = response.choices[0].message.content or ""

    # Send it back as JSON. FastAPI turns this dictionary into a JSON
    # response automatically.
    #
    # The token counts go back too. They are what every cost decision is
    # judged on, so it's worth having them visible rather than buried in
    # a log somewhere.
    return {
        "reply": reply_text,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,          # what we sent
            "completion_tokens": response.usage.completion_tokens,  # what it wrote
            "total_tokens": response.usage.total_tokens,
        },
    }
