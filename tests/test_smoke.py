"""Smoke tests — no API key, no network, no cost.

The model call is replaced with a stub, so these check OUR code: that the
app wires together, that bad input is rejected, and that the policy still
contains the facts the assistant is supposed to quote.
"""

from types import SimpleNamespace

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
    """A test client whose model call is stubbed out."""
    monkeypatch.setattr(llm, "chat", lambda **kwargs: fake_response())
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

    def capture(**kwargs):
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
