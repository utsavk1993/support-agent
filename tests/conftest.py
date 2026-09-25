"""Shared test setup.

Tests run against a REAL Postgres, not a stand-in. Locally that is the
container from docker-compose.yml; in CI it is a service container GitHub
starts for the job. Using the same engine as production is the only way to
catch what differs between databases.

A NOTE ON EVENT LOOPS
---------------------
asyncpg connections belong to the event loop that created them, and using
one from a different loop fails with "another operation is in progress".
That matters here because `asyncio.run()` creates a fresh loop every call,
and TestClient runs the app on a loop of its own.

So there are two separate paths to the database, deliberately:

  the app      opens its pool through the normal startup code, on whichever
               loop TestClient is using
  the tests    open a short-lived connection of their own whenever they need
               to look at a table directly

They never share a connection, so the loops never collide.
"""

import asyncio
import os

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://northwind:local-development-only@127.0.0.1:5433/support",
)
os.environ.setdefault("NVIDIA_API_KEY", "dummy-key-not-used-in-tests")
os.environ.setdefault("SECRET_KEY", "test-only-secret")

import asyncpg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import llm  # noqa: E402
import main  # noqa: E402


# ---------------------------------------------------------------------------
# TALKING TO THE DATABASE FROM A TEST
# ---------------------------------------------------------------------------
def db(sql: str, *args):
    """Run one statement on a connection of its own, and return the rows.

    Deliberately opens and closes a connection each time. It is slower than
    reusing one, and irrelevant at this scale, but it means a test can never
    borrow a connection from the wrong event loop.
    """
    async def run():
        connection = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            if sql.strip().upper().startswith("SELECT"):
                return await connection.fetch(sql, *args)
            return await connection.execute(sql, *args)
        finally:
            await connection.close()

    return asyncio.run(run())


@pytest.fixture(scope="session", autouse=True)
def fresh_schema():
    """Start the run from nothing.

    Dropping the tables means the migrations run for real on every test run,
    so a broken migration fails here rather than on a deploy.
    """
    db("""
        DROP TABLE IF EXISTS messages CASCADE;
        DROP TABLE IF EXISTS conversations CASCADE;
        DROP TABLE IF EXISTS schema_migrations CASCADE;
    """)


@pytest.fixture
def clean_db():
    """Empty the tables after each test, so order never matters.

    TRUNCATE ... CASCADE clears messages along with conversations in one
    statement, and is far quicker than deleting row by row.
    """
    yield
    db("TRUNCATE conversations CASCADE")


# ---------------------------------------------------------------------------
# A BROWSER
# ---------------------------------------------------------------------------
async def fake_stream(pieces=None, fail_after=None):
    """Stand in for llm.stream — yields the pieces a real stream would.

    `fail_after` makes it break partway through, so we can check what the
    client sees when a reply dies after it has already started.
    """
    if pieces is None:
        pieces = [
            {"type": "thinking", "text": "Customer asks about "},
            {"type": "thinking", "text": "returning a used tool."},
            {"type": "text", "text": "A used power tool "},
            {"type": "text", "text": "carries a 15% "},
            {"type": "text", "text": "restocking fee."},
            {"type": "usage", "prompt_tokens": 100, "completion_tokens": 20,
             "total_tokens": 120, "thinking_tokens": 12, "answer_tokens": 8,
             "split_is_estimated": True},
        ]
    for index, piece in enumerate(pieces):
        if fail_after is not None and index == fail_after:
            raise RuntimeError("provider went away mid-reply")
        yield piece


@pytest.fixture
def client(monkeypatch, clean_db):
    """One browser, with the model stubbed out.

    Used as a context manager so FastAPI's startup and shutdown actually
    run — that is what opens the database pool, on TestClient's own loop.

    TestClient keeps cookies between requests, so it behaves like a single
    visitor: the session cookie set on the first request comes back on the
    next, and conversations stay owned by the same person.
    """
    monkeypatch.setattr(llm, "stream", lambda **kwargs: fake_stream())
    with TestClient(main.app) as test_client:
        yield test_client
