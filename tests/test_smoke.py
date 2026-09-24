"""Smoke tests — no API key, no network, no cost.

The model call is replaced with a stub, so these check OUR code: that the
app wires together, that bad input is rejected, and that the policy still
contains the facts the assistant is supposed to quote.
"""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import llm
import main
from prompts import SUPPORT_POLICY


def fake_response(reply="A used power tool carries a 15% restocking fee.",
                  prompt_tokens=100, completion_tokens=20):
    """Stand in for a model reply, shaped like the real response object.

    SimpleNamespace turns keyword arguments into attributes, so this reads
    the same way the real thing does: response.choices[0].message.content.
    """
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


@pytest.fixture
def client(monkeypatch):
    """A test client whose model call is stubbed out.

    The stub is `async def` because the handler awaits it. A plain function
    here fails with "can't be used in 'await' expression".
    """
    async def fake_chat(**kwargs):
        return fake_response()

    monkeypatch.setattr(llm, "chat", fake_chat)
    return TestClient(main.app)


def test_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Northwind" in response.text


def test_chat_returns_reply_and_usage(client):
    response = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "restocking fee?"}]
    })
    assert response.status_code == 200

    body = response.json()
    assert body["reply"]
    assert body["usage"]["prompt_tokens"] == 100
    assert body["usage"]["completion_tokens"] == 20
    assert body["usage"]["total_tokens"] == 120


def test_policy_is_sent_as_the_first_message(client, monkeypatch):
    """The policy must lead the prompt, or prefix caching cannot work."""
    captured = {}

    async def capture(**kwargs):
        captured.update(kwargs)
        return fake_response()

    monkeypatch.setattr(llm, "chat", capture)
    client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    first = captured["messages"][0]
    assert first["role"] == "system"
    assert first["content"] == SUPPORT_POLICY


@pytest.mark.parametrize("bad_body", [
    {"messages": []},                                # empty conversation
    {"messages": [{"role": "user"}]},                # missing content
    {"messages": [{"role": "user", "content": 1}]},  # content not a string
    {"nonsense": True},                              # wrong shape entirely
])
def test_malformed_requests_are_rejected(client, bad_body):
    assert client.post("/api/chat", json=bad_body).status_code == 422


def test_message_model_requires_both_fields():
    with pytest.raises(ValidationError):
        main.Message(role="user")


@pytest.mark.parametrize("fact", [
    "15% restocking fee",
    "30 days",
    "3-year limited warranty",
    "75 dollars",
    "Never invent an exception",
])
def test_policy_contains_key_facts(fact):
    assert fact in SUPPORT_POLICY


def test_requests_are_handled_concurrently(monkeypatch):
    """Requests should overlap, not queue up behind one another.

    This is the regression test for the blocking request path. With a sync
    handler, FastAPI gives each request one thread from a pool of 40 and
    holds it for the whole request, so 50 at once would run as two batches
    and take roughly twice the delay. Async holds no thread while waiting.
    """
    delay = 0.2
    requests = 50

    async def slow_chat(**kwargs):
        await asyncio.sleep(delay)
        return fake_response()

    monkeypatch.setattr(llm, "chat", slow_chat)

    async def fire_all_at_once():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as sender:
            body = {"messages": [{"role": "user", "content": "hi"}]}
            started = time.perf_counter()
            responses = await asyncio.gather(
                *[sender.post("/api/chat", json=body) for _ in range(requests)]
            )
            return time.perf_counter() - started, responses

    elapsed, responses = asyncio.run(fire_all_at_once())

    assert all(r.status_code == 200 for r in responses)

    # One after another would be 50 x 0.2s = 10s. Overlapping should land
    # near 0.2s; allow generous headroom so a slow CI runner doesn't flake.
    assert elapsed < delay * 5, f"{requests} requests took {elapsed:.2f}s — they queued"
