"""Smoke tests — no API key, no network, no model calls.

The model is stubbed in conftest.py, so these check OUR code: that the app
wires together, that bad input is rejected, that the streaming contract
holds, and that the policy still contains the facts the assistant quotes.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from conftest import fake_stream
from pydantic import ValidationError

import llm
import main
import store
from prompts import SUPPORT_POLICY


def send(client, message="restocking fee?", conversation_id=None):
    """POST one message, the way the browser does."""
    body = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat", json=body)


def read_events(response):
    """Pull the JSON objects out of a Server-Sent Events response body."""
    return [
        json.loads(block[6:])
        for block in response.text.split("\n\n")
        if block.startswith("data: ")
    ]


def reply_events(response):
    """Everything except the conversation announcement."""
    return [e for e in read_events(response) if e["type"] != "conversation"]


def test_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Northwind" in response.text


def test_reply_arrives_in_pieces(client):
    """The point of streaming: several text events, not one block."""
    response = send(client)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    texts = [e for e in reply_events(response) if e["type"] == "text"]

    assert len(texts) > 1, "a single event means it is not really streaming"
    assert "".join(t["text"] for t in texts) == (
        "A used power tool carries a 15% restocking fee."
    )


def test_usage_arrives_last(client):
    """Token counts are not in the pieces; they come in a final event."""
    response = send(client)
    usage = reply_events(response)[-1]
    assert usage["type"] == "usage"
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 20

    # The displayed breakdown has to add up to the billed total, or the
    # numbers under each reply are quietly wrong.
    assert usage["thinking_tokens"] + usage["answer_tokens"] == usage["completion_tokens"]
    assert (usage["prompt_tokens"] + usage["thinking_tokens"]
            + usage["answer_tokens"]) == usage["total_tokens"]


def test_failure_before_the_reply_starts_is_a_normal_http_error(client, monkeypatch):
    """Nothing sent yet, so we can still use a status code."""
    async def refuses_to_start(**kwargs):
        raise RuntimeError("provider is down")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(llm, "stream", lambda **kwargs: refuses_to_start())

    response = send(client, "hi")
    assert response.status_code == 502
    assert "provider" not in response.text.lower(), "internal detail leaked"


def test_failure_midway_arrives_inside_the_stream(client, monkeypatch):
    """Too late for a status code, so the error has to travel as an event."""
    monkeypatch.setattr(llm, "stream", lambda **kwargs: fake_stream(fail_after=2))

    response = send(client, "hi")
    assert response.status_code == 200, "the stream already committed to 200"

    events = reply_events(response)
    assert [e["type"] for e in events] == ["thinking", "thinking", "error"]
    assert "went away" not in events[-1]["message"], "internal detail leaked"


def test_policy_is_sent_as_the_first_message(client, monkeypatch):
    """The policy must lead the prompt, or prefix caching cannot work."""
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return fake_stream()

    monkeypatch.setattr(llm, "stream", capture)
    send(client, "hi")

    first = captured["messages"][0]
    assert first["role"] == "system"
    assert first["content"] == SUPPORT_POLICY


@pytest.mark.parametrize("bad_body", [
    {"message": ""},              # empty message
    {"message": 1},               # not a string
    {"message": "x" * 5000},      # beyond the length cap
    {"nonsense": True},           # wrong shape entirely
])
def test_malformed_requests_are_rejected(client, bad_body):
    assert client.post("/api/chat", json=bad_body).status_code == 422


def test_chat_request_requires_a_message():
    with pytest.raises(ValidationError):
        main.ChatRequest(conversation_id="abc")


@pytest.mark.parametrize("fact", [
    "15% restocking fee",
    "30 days",
    "3-year limited warranty",
    "75 dollars",
    "Never invent an exception",
])
def test_policy_contains_key_facts(fact):
    assert fact in SUPPORT_POLICY


def test_requests_are_handled_concurrently(monkeypatch, clean_db):
    """Requests should overlap, not queue up behind one another.

    This is the regression test for the blocking request path. With a sync
    handler, FastAPI gives each request one thread from a pool of 40 and
    holds it for the whole request, so 50 at once would run as two batches
    and take roughly twice the delay. Async holds no thread while waiting.
    """
    delay = 0.2
    requests = 50

    async def slow_stream(**kwargs):
        await asyncio.sleep(delay)
        yield {"type": "text", "text": "ok"}

    monkeypatch.setattr(llm, "stream", lambda **kwargs: slow_stream())

    async def fire_all_at_once():
        # httpx does not run FastAPI's startup, so the pool is opened by hand
        # here — on this loop, which is the one the requests will use.
        await store.connect()
        try:
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as sender:
                body = {"message": "hi"}
                started = time.perf_counter()
                responses = await asyncio.gather(
                    *[sender.post("/api/chat", json=body) for _ in range(requests)]
                )
                return time.perf_counter() - started, responses
        finally:
            await store.disconnect()

    elapsed, responses = asyncio.run(fire_all_at_once())

    assert all(r.status_code == 200 for r in responses)

    # One after another would be 50 x 0.2s = 10s. Overlapping should land
    # near 0.2s; allow generous headroom so a slow CI runner doesn't flake.
    assert elapsed < delay * 5, f"{requests} requests took {elapsed:.2f}s — they queued"


def test_reasoning_is_labelled_separately_from_the_reply(client):
    """Thinking and answer must stay distinguishable.

    The client folds reasoning away behind a toggle. That only works if the
    two never get mixed into the same event type.
    """
    response = send(client, "hi")
    events = reply_events(response)

    kinds = [e["type"] for e in events]
    assert "thinking" in kinds
    assert "text" in kinds

    # All reasoning arrives before any of the answer.
    assert max(i for i, k in enumerate(kinds) if k == "thinking") < kinds.index("text")

    thinking = " ".join(e["text"] for e in events if e["type"] == "thinking")
    reply = "".join(e["text"] for e in events if e["type"] == "text")
    assert thinking not in reply, "reasoning leaked into the visible answer"


def test_thinking_tokens_are_estimated_from_how_much_was_reasoning(monkeypatch):
    """The provider bills one output figure covering reasoning AND answer.

    We split it by the proportion of characters that were reasoning. Here
    three quarters of the output is thinking, so three quarters of the
    billed output tokens should be attributed to it.
    """
    def chunk(reasoning=None, content=None, usage=None):
        delta = SimpleNamespace(content=content, reasoning_content=reasoning)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=usage)

    class Stream:
        def __aiter__(self):
            async def chunks():
                yield chunk(reasoning="t" * 75)   # 75 characters of thinking
                yield chunk(content="a" * 25)     # 25 characters of answer
                yield SimpleNamespace(choices=[], usage=SimpleNamespace(
                    prompt_tokens=500, completion_tokens=100, total_tokens=600))
            return chunks()

    async def create(**kwargs):
        return Stream()

    # stream() calls the client directly, so that is what gets stubbed —
    # the retry now wraps opening the stream AND reading its first chunk.
    monkeypatch.setattr(llm.client.chat.completions, "create", create)

    async def collect():
        return [piece async for piece in llm.stream(model="x", messages=[])]

    usage = asyncio.run(collect())[-1]

    assert usage["thinking_tokens"] == 75
    assert usage["answer_tokens"] == 25
    assert usage["split_is_estimated"] is True

    # Billed figures must pass through untouched.
    assert usage["prompt_tokens"] == 500
    assert usage["completion_tokens"] == 100
    assert usage["total_tokens"] == 600
