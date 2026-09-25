"""
store.py — everything that touches the database.

All the SQL in this app lives here. Nothing else knows Postgres exists, so
moving to a different database, or changing how a query works, is a change
to this one file.

There is no ORM. The queries are short enough to read, and seeing the actual
SQL is worth more here than the convenience of hiding it.

A NOTE ON BLOCKING
------------------
We use asyncpg, which is async all the way down. The ordinary Postgres
drivers block, and a blocking call in an async server freezes every request
on the process, not just its own — the same trap as time.sleep. Every
database call below is awaited.
"""

import os
from pathlib import Path

import asyncpg

# Where the migrations live, worked out from this file's location so it
# does not matter which folder you launch the app from.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# One pool for the whole app, opened at startup. Opening a connection per
# request is a well-known way to exhaust a database: connections are
# expensive to establish, and Postgres caps how many can exist at once.
# A pool keeps a handful open and lends them out.
_pool: asyncpg.Pool | None = None


def database_url() -> str:
    """Where to connect. One environment variable, so local, CI and
    production differ by configuration rather than by code."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env, or start "
            "the local database with: docker compose up -d"
        )
    return url


async def connect() -> None:
    """Open the pool and bring the schema up to date. Called once, at startup."""
    global _pool
    _pool = await asyncpg.create_pool(
        database_url(),
        min_size=1,
        # Small on purpose. Every connection costs memory on the database
        # server, and this app is limited by the model provider long before
        # it is limited by Postgres.
        max_size=10,
        # Refuse to hang forever waiting for a free connection.
        command_timeout=30,
    )
    await _migrate()


async def disconnect() -> None:
    """Close the pool cleanly at shutdown, so connections are not left open."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    """The pool, or a clear error if something used the database too early."""
    if _pool is None:
        raise RuntimeError("Database pool is not open. connect() must run first.")
    return _pool


# ---------------------------------------------------------------------------
# MIGRATIONS
# ---------------------------------------------------------------------------
async def _migrate() -> None:
    """Apply any migration files that have not run yet, in filename order.

    The database records which have been applied, so this is safe to run on
    every startup and on every server instance: each file runs exactly once.

    Once a migration has run anywhere, it is never edited. The database has
    no way to notice the change, so the file would no longer describe what is
    actually deployed. Change the schema by adding a new file.
    """
    async with pool().acquire() as connection:
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename   text        PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)

        applied = {
            row["filename"]
            for row in await connection.fetch("SELECT filename FROM schema_migrations")
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue

            # Each migration runs inside a transaction, so a file that fails
            # halfway leaves the schema untouched rather than half-changed.
            async with connection.transaction():
                await connection.execute(path.read_text())
                await connection.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )
            print(f"[migration] applied {path.name}")


# ---------------------------------------------------------------------------
# CONVERSATIONS
# ---------------------------------------------------------------------------
async def create_conversation(conversation_id: str, owner_id: str) -> None:
    """Start a new conversation belonging to someone."""
    await pool().execute(
        "INSERT INTO conversations (id, owner_id) VALUES ($1, $2)",
        conversation_id, owner_id,
    )


async def conversation_belongs_to(conversation_id: str, owner_id: str) -> bool:
    """Is this conversation theirs?

    Every read and write checks this. The owner is part of the query rather
    than something checked afterwards, so there is no path that fetches
    someone else's data and then remembers to compare.
    """
    row = await pool().fetchrow(
        "SELECT 1 FROM conversations WHERE id = $1 AND owner_id = $2",
        conversation_id, owner_id,
    )
    return row is not None


async def load_messages(conversation_id: str, owner_id: str) -> list[dict]:
    """The conversation so far, oldest first, or an empty list if not theirs."""
    rows = await pool().fetch(
        """
        SELECT m.role, m.content
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.conversation_id = $1 AND c.owner_id = $2
        ORDER BY m.id
        """,
        conversation_id, owner_id,
    )
    return [{"role": row["role"], "content": row["content"]} for row in rows]


async def add_message(
    conversation_id: str,
    role: str,
    content: str,
    usage: dict | None = None,
) -> None:
    """Append one message, and mark the conversation as recently active.

    Both statements run in a transaction: a conversation whose updated_at
    disagrees with its newest message would be a small, confusing lie.
    """
    usage = usage or {}
    async with pool().acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO messages (
                    conversation_id, role, content,
                    prompt_tokens, completion_tokens, thinking_tokens, answer_tokens
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                conversation_id, role, content,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("thinking_tokens"),
                usage.get("answer_tokens"),
            )
            await connection.execute(
                "UPDATE conversations SET updated_at = now() WHERE id = $1",
                conversation_id,
            )


async def conversation_cost(conversation_id: str, owner_id: str) -> dict:
    """What this conversation has cost in tokens so far.

    The reason token counts are stored rather than only displayed: this is a
    question you can answer, not estimate.
    """
    row = await pool().fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE m.role = 'assistant') AS replies,
            coalesce(sum(m.prompt_tokens), 0)     AS prompt_tokens,
            coalesce(sum(m.completion_tokens), 0) AS completion_tokens,
            coalesce(sum(m.thinking_tokens), 0)   AS thinking_tokens,
            coalesce(sum(m.answer_tokens), 0)     AS answer_tokens
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.conversation_id = $1 AND c.owner_id = $2
        """,
        conversation_id, owner_id,
    )
    return dict(row) if row else {}
