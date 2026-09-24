"""
llm.py — the one place that knows how to talk to the model.

Everything in this app goes through this file. Nothing else creates a client
or knows which model we use. That way, switching models, switching providers,
or changing how we handle failures is a change to ONE file.
"""

import os  # Read environment variables (like process.env in Node), and build file paths.
import random  # Adds a random bit to the retry wait, so every copy of the app doesn't retry at the same instant.
import time  # Pauses between retries, so we don't hammer a struggling server.

from dotenv import load_dotenv  # Reads the .env file holding our API key. .env is listed in .gitignore, so it never gets committed.
from openai import (
    APIConnectionError,  # never reached the server at all
    APITimeoutError,  # reached it, but it never replied
    InternalServerError,  # the server broke. Their fault, not ours
    OpenAI,
    RateLimitError,  # we're sending too fast
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
client = OpenAI(
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
#   "the server had a bad moment"  -> try again, it'll probably work
#   "you asked for something wrong" -> don't bother, you'll get the same no
#
# Only the first kind is listed here. Everything else fails immediately,
# which is what we want — a typo should be loud, not quietly retried.
RETRYABLE = (InternalServerError, RateLimitError, APITimeoutError, APIConnectionError)

# Four goes in total: the first try, plus three more.
MAX_ATTEMPTS = 4

# How long to wait after the first failure. Each later wait doubles it:
# 0.6s, then 1.2s, then 2.4s, then give up.
#
# We wait at all because hammering a struggling server doesn't help anyone.
# We wait longer each time because if it needs a few seconds to recover,
# a short fixed wait burns all our tries before it's ready.
BACKOFF_BASE = 0.6

def chat(**kwargs):
    """Send a request to the model. If it fails for a silly reason, try again.

    Use it exactly like the normal call — same arguments in, same answer out.
    The retrying is invisible except for a printed line when it happens.
    """
    # Somewhere to keep the last error we saw.
    #
    # Python throws away the `e` in `except ... as e` the moment that block
    # ends, so if we want to re-raise it later we have to copy it somewhere
    # that survives. That's all this line does.
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # The real call. If it works, we return here and the retry code
            # below never runs.
            return client.chat.completions.create(**kwargs)

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
            print(f"  [retry {attempt}/{MAX_ATTEMPTS - 1}] "
                  f"{type(e).__name__} - waiting {delay:.1f}s")

            time.sleep(delay)

    # Everything failed. Throw the real error so the caller sees exactly what
    # went wrong. Returning nothing here would hide the problem.
    raise last_error
