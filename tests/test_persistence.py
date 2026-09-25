"""Tests for storing conversations.

These run against a real Postgres — see conftest.py. They cover the things
that would be embarrassing to get wrong: losing a conversation, showing one
person another person's messages, or billing for tokens nobody recorded.
"""

import json

import pytest
from conftest import db
from fastapi.testclient import TestClient

import llm
import main


async def fake_stream(**kwargs):
    for piece in [
        {"type": "text", "text": "Used power tools "},
        {"type": "text", "text": "carry a 15% fee."},
        {"type": "usage", "prompt_tokens": 684, "completion_tokens": 100,
         "total_tokens": 784, "thinking_tokens": 40, "answer_tokens": 60,
         "split_is_estimated": True},
    ]:
        yield piece


async def dies_halfway(**kwargs):
    yield {"type": "text", "text": "Used power tools "}
    raise RuntimeError("provider went away")


@pytest.fixture
def visitor(monkeypatch, clean_db):
    """One browser, with the model stubbed. Used as a context manager so
    FastAPI's startup runs and the database pool is opened."""
    monkeypatch.setattr(llm, "stream", lambda **kwargs: fake_stream())
    with TestClient(main.app) as test_client:
        yield test_client


def send(client, message, conversation_id=None):
    body = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat", json=body)


def events(response):
    return [json.loads(b[6:]) for b in response.text.split("\n\n") if b.startswith("data: ")]


def conversation_id_of(response):
    return next(e["id"] for e in events(response) if e["type"] == "conversation")


# --- the point of the whole exercise ----------------------------------------

def test_a_conversation_survives_a_refresh(visitor):
    """Close the tab, come back, and the conversation is still there."""
    first = send(visitor, "Can I return a used power tool?")
    conversation_id = conversation_id_of(first)

    # A refresh is just this request, with the id the browser remembered.
    restored = visitor.get(f"/api/conversations/{conversation_id}")
    assert restored.status_code == 200

    messages = restored.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Can I return a used power tool?"
    assert messages[1]["content"] == "Used power tools carry a 15% fee."


def test_history_comes_from_the_database_not_the_browser(visitor, monkeypatch):
    """The browser sends one message; the server supplies the rest."""
    first = send(visitor, "Can I return a used power tool?")
    conversation_id = conversation_id_of(first)

    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return fake_stream()

    monkeypatch.setattr(llm, "stream", capture)
    send(visitor, "What about shipping?", conversation_id)

    roles = [m["role"] for m in captured["messages"]]
    # system policy, then the full stored conversation, then the new message
    assert roles == ["system", "user", "assistant", "user"]
    assert captured["messages"][-1]["content"] == "What about shipping?"


# --- one person cannot read another's conversation --------------------------

def test_another_visitor_cannot_load_your_conversation(visitor, monkeypatch):
    conversation_id = conversation_id_of(send(visitor, "my private question"))

    # A different TestClient means a different cookie jar, so a different
    # session — the equivalent of someone else's browser.
    monkeypatch.setattr(llm, "stream", lambda **kwargs: fake_stream())
    with TestClient(main.app) as stranger:
        response = stranger.get(f"/api/conversations/{conversation_id}")

    # 404 rather than 403: a 403 would confirm this conversation exists,
    # which lets someone probe for valid ids.
    assert response.status_code == 404
    assert "my private question" not in response.text


def test_another_visitor_cannot_post_into_your_conversation(visitor, monkeypatch):
    conversation_id = conversation_id_of(send(visitor, "mine"))

    monkeypatch.setattr(llm, "stream", lambda **kwargs: fake_stream())
    with TestClient(main.app) as stranger:
        assert send(stranger, "sneaking in", conversation_id).status_code == 404


def test_an_unknown_conversation_id_is_a_404(visitor):
    assert visitor.get("/api/conversations/11111111-2222-3333-4444-555555555555").status_code == 404


# --- what gets written, and when --------------------------------------------

def test_token_counts_are_stored_per_message(visitor):
    conversation_id = conversation_id_of(send(visitor, "restocking fee?"))

    rows = db(
        """
        SELECT prompt_tokens, completion_tokens, thinking_tokens, answer_tokens
        FROM messages WHERE role = 'assistant' AND conversation_id = $1::uuid
        """,
        conversation_id,
    )
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] == 684
    assert rows[0]["thinking_tokens"] == 40
    assert rows[0]["answer_tokens"] == 60


def test_a_reply_that_dies_halfway_still_keeps_what_was_shown(visitor, monkeypatch):
    """The customer read that text. A reload should show it too."""
    monkeypatch.setattr(llm, "stream", lambda **kwargs: dies_halfway())

    response = send(visitor, "Can I return a used power tool?")
    conversation_id = conversation_id_of(response)

    kinds = [e["type"] for e in events(response)]
    assert "error" in kinds

    messages = visitor.get(f"/api/conversations/{conversation_id}").json()["messages"]
    assert messages[0]["content"] == "Can I return a used power tool?"
    assert messages[1]["content"] == "Used power tools "


def test_the_question_survives_a_model_failure(visitor, monkeypatch):
    """Saved before the model is called, so a failure cannot lose it."""
    async def refuses(**kwargs):
        raise RuntimeError("provider is down")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(llm, "stream", lambda **kwargs: refuses())

    assert send(visitor, "a question that fails").status_code == 502

    rows = db("SELECT content FROM messages ORDER BY id DESC LIMIT 1")
    assert rows[0]["content"] == "a question that fails"


# --- migrations -------------------------------------------------------------

def test_migrations_run_once_and_are_recorded(visitor):
    applied = [r["filename"] for r in db("SELECT filename FROM schema_migrations")]
    assert "001_conversations.sql" in applied
    assert len(applied) == len(set(applied)), "a migration was applied twice"
