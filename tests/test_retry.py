"""Tests for which failures get retried.

The distinction matters in both directions. Not retrying a transient failure
shows the user an error for something that would have worked; retrying a
permanent one delays the real problem and buries it.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIError, AuthenticationError, BadRequestError, NotFoundError

import llm


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Skip the backoff, so these run instantly instead of over four seconds."""
    async def instant(seconds):
        return

    monkeypatch.setattr(llm.asyncio, "sleep", instant)


def api_error(message="Service temporarily overloaded"):
    """The bare APIError the library raises for a problem inside a stream.

    This is the exact shape that used to slip past the retry logic: it is
    the base class, so a list of specific error types never matched it.
    """
    return APIError(message, request=httpx.Request("POST", "https://example.test"), body=None)


def status_error(cls, code):
    response = httpx.Response(code, request=httpx.Request("POST", "https://example.test"))
    return cls("nope", response=response, body=None)


# --- the bug this file exists for -------------------------------------------

def test_a_bare_api_error_is_retried():
    """The failure that used to be reported to the user without a single retry."""
    attempts = {"n": 0}

    async def fails_twice_then_works():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise api_error()
        return "finally"

    result = asyncio.run(llm._with_retry(fails_twice_then_works, "test"))

    assert result == "finally"
    assert attempts["n"] == 3


def test_a_provider_that_stays_broken_eventually_gives_up():
    attempts = {"n": 0}

    async def always_fails():
        attempts["n"] += 1
        raise api_error()

    with pytest.raises(APIError):
        asyncio.run(llm._with_retry(always_fails, "test"))

    assert attempts["n"] == llm.MAX_ATTEMPTS


# --- our own mistakes must not be retried -----------------------------------

@pytest.mark.parametrize("error", [
    status_error(BadRequestError, 400),
    status_error(AuthenticationError, 401),
    status_error(NotFoundError, 404),
])
def test_permanent_failures_fail_immediately(error):
    """Retrying a bad key or a typo only delays the real problem."""
    attempts = {"n": 0}

    async def always_fails():
        attempts["n"] += 1
        raise error

    with pytest.raises(type(error)):
        asyncio.run(llm._with_retry(always_fails, "test"))

    assert attempts["n"] == 1, "a permanent failure was retried"


# --- the retry has to reach the first chunk ---------------------------------

def test_a_stream_that_fails_on_its_first_read_is_retried(monkeypatch):
    """Where a busy provider actually reports itself.

    Opening a stream succeeds even when the provider is overloaded, because
    the connection comes up before the model writes anything. The failure
    lands on the first read. Retrying only the opening caught nothing.
    """
    attempts = {"n": 0}

    def chunk(content=None, usage=None):
        delta = SimpleNamespace(content=content, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=usage)

    class Stream:
        """Fails on the first read the first two times it is opened."""
        def __aiter__(self):
            async def chunks():
                if attempts["n"] < 3:
                    raise api_error()
                yield chunk(content="it worked")
                yield SimpleNamespace(choices=[], usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=2, total_tokens=12))
            return chunks()

    async def create(**kwargs):
        attempts["n"] += 1
        return Stream()          # opening always succeeds

    monkeypatch.setattr(llm.client.chat.completions, "create", create)

    async def collect():
        return [piece async for piece in llm.stream(model="x", messages=[])]

    pieces = asyncio.run(collect())

    assert attempts["n"] == 3, "the first read was not retried"
    assert pieces[0] == {"type": "text", "text": "it worked"}


def test_the_first_chunk_is_not_lost_when_it_succeeds(monkeypatch):
    """Fetching a chunk early to test it must not swallow it."""
    def chunk(content):
        delta = SimpleNamespace(content=content, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)

    class Stream:
        def __aiter__(self):
            async def chunks():
                yield chunk("first ")
                yield chunk("second")
            return chunks()

    async def create(**kwargs):
        return Stream()

    monkeypatch.setattr(llm.client.chat.completions, "create", create)

    async def collect():
        return [piece async for piece in llm.stream(model="x", messages=[])]

    texts = [p["text"] for p in asyncio.run(collect()) if p["type"] == "text"]
    assert "".join(texts) == "first second"
