"""
llm.py — the one place that knows how to talk to the model.

Everything in this app goes through this file. Nothing else creates a client
or knows which model we use. That way, switching models, switching providers,
or changing how we handle failures is a change to ONE file.
"""

import asyncio  # Pauses between retries WITHOUT freezing everyone else. See the note further down.
import os  # Read environment variables (like process.env in Node), and build file paths.
import random  # Adds a random bit to the retry wait, so every copy of the app doesn't retry at the same instant.

from dotenv import load_dotenv  # Reads the .env file holding our API key. .env is listed in .gitignore, so it never gets committed.
from openai import (
    APIError,  # the base class every provider error inherits from
    AsyncOpenAI,  # the non-blocking version of the client
    AuthenticationError,  # our key is wrong
    BadRequestError,  # we sent something malformed
    NotFoundError,  # no such model
    PermissionDeniedError,  # our key is not allowed to do this
    UnprocessableEntityError,  # the request made no sense
)

# Read the .env file sitting next to this one.
#
# We build the path from THIS file's location rather than just saying ".env",
# because a bare ".env" is looked up relative to whatever folder you happened
# to run the command from. This way it works no matter where you launch it.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Which model we're using. One line, one place.
MODEL = "nvidia/nemotron-3-super-120b-a12b"

# Build the connection once, when this file is first imported, and share it.
# Reusing one client keeps the connection to the server open between calls,
# so we don't redo the setup handshake every single time.
# AsyncOpenAI is the same client as OpenAI, with one difference: while it
# waits for a reply, it hands control back so the server can get on with
# other people's requests. The plain OpenAI client just sits there.
client = AsyncOpenAI(
    # This URL is what makes it NVIDIA rather than OpenAI. The library itself
    # doesn't care who it's talking to.
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.getenv("NVIDIA_API_KEY"),

    # If the server hasn't answered in 60 seconds, stop waiting. Without this,
    # a request that never gets a reply hangs forever, and takes our app
    # with it — no error, no crash, just stuck.
    timeout=60.0,

    # Turn off the library's own retrying, because we do our own below.
    # Theirs is silent, so you'd never learn the server is unreliable.
    max_retries=0,
)

# Failures split into two kinds, and they need opposite reactions:
#
#   "you asked for something wrong" -> don't bother, you'll get the same no
#   "the server had a bad moment"   -> try again, it'll probably work
#
# We list the PERMANENT ones and treat everything else as worth retrying.
#
# That way round on purpose. Listing what to retry looks tidier but quietly
# misses things: the library raises a plain APIError when a provider reports
# a problem partway through a stream, and a plain APIError is not an instance
# of any specific error type, so a list of specific types never matches it.
# The result was a server that looked like it retried and never did.
#
# Listing the permanent ones cannot fail that way. A new kind of transient
# error gets retried by default, which is the safer way to be wrong.
PERMANENT = (
    BadRequestError,          # 400 — malformed; sending it again is pointless
    AuthenticationError,      # 401 — wrong key; needs a human
    PermissionDeniedError,    # 403 — not allowed; needs a human
    NotFoundError,            # 404 — no such model; a typo
    UnprocessableEntityError, # 422 — the request made no sense
)

# Anything else the library raises. Timeouts, dropped connections, rate
# limits, 5xx, and the bare APIError that streams produce.
RETRYABLE = (APIError,)

# Four goes in total: the first try, plus three more.
MAX_ATTEMPTS = 4

# How long to wait after the first failure. Each later wait doubles it:
# 0.6s, then 1.2s, then 2.4s, then give up.
#
# We wait at all because hammering a struggling server doesn't help anyone.
# We wait longer each time because if it needs a few seconds to recover,
# a short fixed wait burns all our tries before it's ready.
BACKOFF_BASE = 0.6

async def _with_retry(attempt_once, what: str):
    """Run something, and try again if it fails for a silly reason.

    `attempt_once` is an async function taking no arguments. It gets called
    afresh each try, so whatever it does is started from scratch rather than
    resumed halfway.
    """
    # Somewhere to keep the last error we saw.
    #
    # Python throws away the `e` in `except ... as e` the moment that block
    # ends, so if we want to re-raise it later we have to copy it somewhere
    # that survives. That's all this line does.
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await attempt_once()

        except PERMANENT:
            # Our mistake. Retrying would fail identically, just slower, and
            # would bury the real problem under a delay. Let it through.
            raise

        except RETRYABLE as e:
            last_error = e

            # Out of goes? Stop looping — no point sleeping before giving up.
            if attempt == MAX_ATTEMPTS:
                break

            # How long to wait. Two parts:
            #   the doubling      0.6 -> 1.2 -> 2.4  (** means "to the power of")
            #   a random extra    up to 0.2 seconds
            #
            # The random bit stops a crowd forming. If lots of copies of this
            # app hit the same glitch at once, they'd all wait the same amount
            # and all come back together, knocking the server over again.
            delay = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.2)

            # Say it out loud. Silent retries make things feel mysteriously
            # slow and teach you nothing.
            print(f"  [retry {attempt}/{MAX_ATTEMPTS - 1}] {what}: "
                  f"{type(e).__name__} - waiting {delay:.1f}s")

            # THE MOST IMPORTANT LINE IN THIS FILE.
            #
            # time.sleep() would freeze the ENTIRE server for this long —
            # every user, mid-sentence, not just the one who hit the error.
            # One unlucky retry and everybody stops.
            #
            # asyncio.sleep() says "wake me in a moment, go do something
            # useful meanwhile". Same pause for this request, no effect on
            # anyone else.
            await asyncio.sleep(delay)

    # Everything failed. Throw the real error so the caller sees exactly what
    # went wrong. Returning nothing here would hide the problem.
    raise last_error


async def chat(**kwargs):
    """Make one request to the model, retrying if it fails for a silly reason.

    WHO USES THIS
    -------------
    Nothing outside this file calls it directly any more — the app talks to
    `stream()` below, because replies are sent to the browser as they are
    written. It stays public because not every request wants streaming:
    summarising an old conversation, for instance, has no user waiting on it
    word by word, and would use this.

    ABOUT async
    -----------
    `async def` means this function can pause in the middle. While it is
    paused waiting for the model, the server is free to serve other people
    instead of sitting idle. Because it can pause, callers write `await`.
    """
    return await _with_retry(
        lambda: client.chat.completions.create(**kwargs), "request"
    )


async def stream(**kwargs):
    """Send a request and hand back the reply in pieces, as it is written.

    Use it with `async for`, which is the looping version of `await` — it
    pauses at each step until the next piece arrives:

        async for piece in llm.stream(model=..., messages=[...]):
            ...

    Each piece is a small dictionary, so callers never deal with the
    provider's own chunk format:

        {"type": "thinking", "text": "User wants the fee for"}
        {"type": "text",     "text": "The restocking"}
        {"type": "usage",    "prompt_tokens": 689, ...}

    Thinking pieces come first, while the model works out what to say. Text
    pieces follow. The usage piece arrives once, at the very end.

    A note on the token counts. The provider reports ONE figure for
    everything the model wrote, with reasoning and answer lumped together —
    it does not break them out. Since reasoning is often the larger half,
    that hides where the money actually goes. So we measure how much of the
    output was reasoning and split the figure by that proportion. The
    result is an estimate, flagged as such, and the billed totals are
    reported unchanged alongside it.
    """
    # Two settings that must not be left to the caller.
    #
    # stream=True is what makes the model send as it writes instead of all
    # at once.
    #
    # stream_options is easy to miss and expensive to forget. Token counts
    # are NOT in the pieces; they come in one final message, and only if we
    # ask for them here. Leave this out and cost tracking silently stops
    # working, with no error to tell you.
    request = {**kwargs, "stream": True, "stream_options": {"include_usage": True}}

    # WHERE THE RETRY STOPS, AND WHY IT REACHES THIS FAR
    #
    # Opening a stream returns the moment the connection is up, before the
    # model has written anything — so a provider that is busy does not fail
    # at that point, it fails at the FIRST READ. Retrying only the opening
    # therefore catches almost nothing, which is exactly the bug this
    # replaces.
    #
    # So the retry covers opening the stream AND reading its first chunk.
    # Both happen before a single byte reaches the browser, so starting over
    # is invisible to the user.
    #
    # After that we are past the point of no return: those bytes are already
    # on the page and cannot be withdrawn. A failure from here on is
    # surfaced rather than quietly retried.
    async def open_and_read_first():
        opened = await client.chat.completions.create(**request)
        chunks = opened.__aiter__()
        # anext() runs the stream far enough to get the first chunk, which
        # is where a busy provider actually reports itself.
        return chunks, await anext(chunks, None)

    chunks, first_chunk = await _with_retry(open_and_read_first, "stream")
    # How much of the output was the model thinking, versus answering.
    # Used to estimate the token split, since the provider will not tell us.
    thinking_chars = 0
    answer_chars = 0

    async def everything():
        """The first chunk we already fetched, then the rest."""
        if first_chunk is not None:
            yield first_chunk
        async for rest in chunks:
            yield rest

    async for chunk in everything():
        # The final chunk carries usage and has no text.
        if chunk.usage:
            written = chunk.usage.completion_tokens
            measured = thinking_chars + answer_chars

            # Split the billed output tokens in the same proportion as the
            # text we actually saw. Rough, but it answers the question that
            # matters: is reasoning or answering costing me more?
            if measured > 0:
                thinking_tokens = round(written * thinking_chars / measured)
            else:
                thinking_tokens = 0

            yield {
                "type": "usage",
                # Exact, straight from the provider.
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "total_tokens": chunk.usage.total_tokens,
                # Estimated, by the proportion explained above.
                "thinking_tokens": thinking_tokens,
                "answer_tokens": written - thinking_tokens,
                "split_is_estimated": True,
            }

        # `delta` is the NEW text in this chunk, not the whole reply so far.
        # Joining every delta together gives the complete answer.
        if chunk.choices:
            delta = chunk.choices[0].delta

            # Nemotron streams its private scratchpad separately, in
            # `reasoning_content`, before writing any of the real answer.
            # We label it so the browser can keep the two apart and show
            # the reasoning folded away rather than mixed into the reply.
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                thinking_chars += len(reasoning)
                yield {"type": "thinking", "text": reasoning}

            if delta.content:
                answer_chars += len(delta.content)
                yield {"type": "text", "text": delta.content}
