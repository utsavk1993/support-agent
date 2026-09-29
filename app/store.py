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

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import asyncpg

from app import config, delivery

# How long a code is good for. Long enough to find the email, short enough
# that one left in an inbox is not a standing key.
CODE_LIFETIME_MINUTES = 10

# Wrong guesses against a single code before it is destroyed.
MAX_CODE_GUESSES = 5

# Verification actions per conversation per hour, of any kind. Without a
# cap, a leaked list of emails and phone numbers can simply be tried at
# speed, and every other control here is decorative.
MAX_ATTEMPTS_PER_HOUR = 10


def _hash_code(code: str) -> str:
    """Store a hash, never the code.

    A copy of the codes table should not be a list of working codes.
    """
    return hashlib.sha256(code.encode()).hexdigest()


# Where the migrations live. Worked out from the project root rather than
# the current directory, so it does not matter where you launch from.
MIGRATIONS_DIR = config.ROOT / "migrations"

# One pool for the whole app, opened at startup. Opening a connection per
# request is a well-known way to exhaust a database: connections are
# expensive to establish, and Postgres caps how many can exist at once.
# A pool keeps a handful open and lends them out.
_pool: asyncpg.Pool | None = None


async def connect() -> None:
    """Open the pool and bring the schema up to date. Called once, at startup."""
    global _pool
    _pool = await asyncpg.create_pool(
        config.DATABASE_URL,
        min_size=1,
        max_size=config.DATABASE_POOL_SIZE,
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

        applied = {row["filename"] for row in await connection.fetch("SELECT filename FROM schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue

            # Each migration runs inside a transaction, so a file that fails
            # halfway leaves the schema untouched rather than half-changed.
            async with connection.transaction():
                await connection.execute(path.read_text())
                await connection.execute("INSERT INTO schema_migrations (filename) VALUES ($1)", path.name)
            print(f"[migration] applied {path.name}")


# ---------------------------------------------------------------------------
# CONVERSATIONS
# ---------------------------------------------------------------------------
async def create_conversation(conversation_id: str, owner_id: str) -> None:
    """Start a new conversation belonging to someone."""
    await pool().execute(
        "INSERT INTO conversations (id, owner_id) VALUES ($1, $2)",
        conversation_id,
        owner_id,
    )


async def conversation_belongs_to(conversation_id: str, owner_id: str) -> bool:
    """Is this conversation theirs?

    Every read and write checks this. The owner is part of the query rather
    than something checked afterwards, so there is no path that fetches
    someone else's data and then remembers to compare.
    """
    row = await pool().fetchrow(
        "SELECT 1 FROM conversations WHERE id = $1 AND owner_id = $2",
        conversation_id,
        owner_id,
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
        conversation_id,
        owner_id,
    )
    return [{"role": row["role"], "content": row["content"]} for row in rows]


async def load_transcript(conversation_id: str, owner_id: str) -> list[dict]:
    """The conversation as the BROWSER needs it: text plus token counts.

    Deliberately separate from load_messages above. That one feeds the
    model, and any extra key in those dictionaries would be sent to the
    provider along with the conversation. Keeping the two apart means a
    display-only field cannot end up in a prompt by accident.
    """
    rows = await pool().fetch(
        """
        SELECT m.role, m.content,
               m.prompt_tokens, m.completion_tokens,
               m.thinking_tokens, m.answer_tokens
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.conversation_id = $1 AND c.owner_id = $2
        ORDER BY m.id
        """,
        conversation_id,
        owner_id,
    )

    transcript = []
    for row in rows:
        message = {"role": row["role"], "content": row["content"]}

        # Only assistant messages have token counts; user messages cost
        # nothing on their own.
        if row["prompt_tokens"] is not None:
            message["usage"] = {
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "thinking_tokens": row["thinking_tokens"],
                "answer_tokens": row["answer_tokens"],
                "total_tokens": row["prompt_tokens"] + row["completion_tokens"],
            }
        transcript.append(message)

    return transcript


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
                conversation_id,
                role,
                content,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("thinking_tokens"),
                usage.get("answer_tokens"),
            )
            await connection.execute(
                "UPDATE conversations SET updated_at = now() WHERE id = $1",
                conversation_id,
            )


# ---------------------------------------------------------------------------
# CUSTOMERS AND ORDERS
#
# Every query below takes the conversation id and reads the verified
# customer from it, rather than taking a customer as an argument.
#
# That is deliberate. If a caller could pass in whose orders to read, the
# model would be choosing — and the model can be talked into things. Here
# there is no code path that reaches another customer's data, whatever the
# model asks for.
# ---------------------------------------------------------------------------
async def _record_attempt(conversation_id: str, outcome: str, email: str | None = None) -> None:
    """Note what was tried, and how it went.

    The email is stored masked. A leak of this table should not hand
    anyone a list of real customer addresses, which is the opposite of
    what an audit trail is for.
    """
    await pool().execute(
        """
        INSERT INTO verification_attempts (conversation_id, email_masked, outcome)
        VALUES ($1, $2, $3)
        """,
        conversation_id,
        delivery._mask_email(email) if email else None,
        outcome,
    )


async def recent_attempt_count(conversation_id: str) -> int:
    """How many attempts this conversation has made in the last hour."""
    return await pool().fetchval(
        """
        SELECT count(*) FROM verification_attempts
        WHERE conversation_id = $1 AND at > now() - interval '1 hour'
        """,
        conversation_id,
    )


async def issue_verification_code(conversation_id: str, email: str, phone: str) -> dict:
    """Match an email and phone, and if they match, send a code.

    Returns the same shape whichever way it goes, so the caller cannot
    tell a real address from an invented one. That matters: a response
    that distinguishes them confirms which addresses are customers.
    """
    if await recent_attempt_count(conversation_id) >= MAX_ATTEMPTS_PER_HOUR:
        await _record_attempt(conversation_id, "rate_limited", email)
        return {"ok": False, "rate_limited": True}

    row = await pool().fetchrow(
        """
        SELECT id, email, phone FROM customers
        WHERE lower(email) = lower($1)
          -- Compare the last ten digits only. Nobody types a phone number
          -- the same way twice — spaces, dashes, brackets, a country code
          -- or not — and refusing a real customer over a leading +1 is a
          -- worse failure than being slightly permissive here. The email
          -- still has to match exactly.
          AND right(regexp_replace(phone, '\\D', '', 'g'), 10)
            = right(regexp_replace($2, '\\D', '', 'g'), 10)
        """,
        email.strip(),
        phone.strip(),
    )

    if row is None:
        # Deliberately indistinguishable from success. No code exists, so
        # nothing can be submitted, and the caller learns nothing.
        await _record_attempt(conversation_id, "no_match", email)
        return {"ok": True, "sent_to": None}

    code = f"{secrets.randbelow(1_000_000):06d}"

    await pool().execute(
        """
        INSERT INTO verification_codes (conversation_id, customer_id, code_hash, expires_at)
        VALUES ($1, $2, $3, now() + $4)
        ON CONFLICT (conversation_id) DO UPDATE
            SET customer_id = EXCLUDED.customer_id,
                code_hash   = EXCLUDED.code_hash,
                issued_at   = now(),
                expires_at  = EXCLUDED.expires_at,
                attempts    = 0
        """,
        conversation_id,
        row["id"],
        _hash_code(code),
        timedelta(minutes=CODE_LIFETIME_MINUTES),
    )

    sent_to = await delivery.deliver_code(code, row["email"], row["phone"])
    await _record_attempt(conversation_id, "code_issued", email)
    return {"ok": True, "sent_to": sent_to}


async def check_verification_code(conversation_id: str, code: str) -> dict:
    """Check a code and, if it is right, mark the conversation verified.

    Every failure returns the same thing. Distinguishing "expired" from
    "wrong" from "none was ever issued" tells someone probing how close
    they are.
    """
    if await recent_attempt_count(conversation_id) >= MAX_ATTEMPTS_PER_HOUR:
        await _record_attempt(conversation_id, "rate_limited")
        return {"ok": False, "rate_limited": True}

    row = await pool().fetchrow(
        "SELECT customer_id, code_hash, expires_at, attempts FROM verification_codes WHERE conversation_id = $1",
        conversation_id,
    )

    if row is None:
        await _record_attempt(conversation_id, "code_rejected")
        return {"ok": False}

    if row["attempts"] >= MAX_CODE_GUESSES or row["expires_at"] < datetime.now(UTC):
        # Burn it either way, so an expired code cannot be guessed at
        # leisure and a spent one cannot be retried.
        await pool().execute("DELETE FROM verification_codes WHERE conversation_id = $1", conversation_id)
        await _record_attempt(conversation_id, "code_expired")
        return {"ok": False}

    # compare_digest rather than ==, so the time taken does not depend on
    # how much of the value was right.
    if not secrets.compare_digest(row["code_hash"], _hash_code(code.strip())):
        await pool().execute(
            "UPDATE verification_codes SET attempts = attempts + 1 WHERE conversation_id = $1",
            conversation_id,
        )
        await _record_attempt(conversation_id, "code_rejected")
        return {"ok": False}

    async with pool().acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE conversations SET verified_customer_id = $1 WHERE id = $2",
                row["customer_id"],
                conversation_id,
            )
            await connection.execute("DELETE FROM verification_codes WHERE conversation_id = $1", conversation_id)

    await _record_attempt(conversation_id, "code_accepted")
    customer = await verified_customer(conversation_id)
    return {"ok": True, "customer": customer}


async def verified_customer(conversation_id: str) -> dict | None:
    """Who this conversation has proved itself to be, if anyone."""
    row = await pool().fetchrow(
        """
        SELECT c.id, c.name, c.email
        FROM conversations v
        JOIN customers c ON c.id = v.verified_customer_id
        WHERE v.id = $1
        """,
        conversation_id,
    )
    return {"id": str(row["id"]), "name": row["name"], "email": row["email"]} if row else None


async def record_access(conversation_id: str, action: str, subject: str | None = None) -> None:
    """Note that this conversation read something, and what."""
    await pool().execute(
        """
        INSERT INTO access_log (conversation_id, customer_id, action, subject)
        SELECT $1, verified_customer_id, $2, $3 FROM conversations WHERE id = $1
        """,
        conversation_id,
        action,
        subject,
    )


async def find_order(conversation_id: str, number: str) -> dict | None:
    """One order, only if it belongs to this conversation's customer.

    An order that exists but belongs to someone else returns None, exactly
    as one that does not exist. The caller cannot tell the two apart, and
    neither can anyone probing order numbers.
    """
    order = await pool().fetchrow(
        """
        SELECT o.number, o.placed_at, o.delivered_at, o.status,
               o.total_cents, o.shipping
        FROM orders o
        JOIN conversations v ON v.verified_customer_id = o.customer_id
        WHERE v.id = $1 AND upper(o.number) = upper($2)
        """,
        conversation_id,
        number.strip(),
    )
    if order is None:
        return None

    items = await pool().fetch(
        """
        SELECT id, description, category, quantity, unit_cents
        FROM order_items WHERE order_number = $1 ORDER BY id
        """,
        order["number"],
    )
    returns = await pool().fetch(
        "SELECT item_id, status FROM returns WHERE order_number = $1",
        order["number"],
    )
    returned = {row["item_id"]: row["status"] for row in returns}

    return {
        **dict(order),
        "items": [{**dict(item), "return_status": returned.get(item["id"])} for item in items],
    }


async def list_orders(conversation_id: str) -> list[dict]:
    """Every order belonging to this conversation's customer, newest first.

    So the assistant can help someone who cannot remember their order
    number, which is most people.
    """
    rows = await pool().fetch(
        """
        SELECT o.number, o.placed_at, o.status, o.total_cents
        FROM orders o
        JOIN conversations v ON v.verified_customer_id = o.customer_id
        WHERE v.id = $1
        ORDER BY o.placed_at DESC
        """,
        conversation_id,
    )
    return [dict(row) for row in rows]


async def find_shipment(conversation_id: str, number: str) -> dict | None:
    """Tracking for an order, scoped the same way."""
    row = await pool().fetchrow(
        """
        SELECT s.carrier, s.tracking, s.shipped_at, s.expected_at, s.delivered_at,
               o.status AS order_status
        FROM shipments s
        JOIN orders o ON o.number = s.order_number
        JOIN conversations v ON v.verified_customer_id = o.customer_id
        WHERE v.id = $1 AND upper(s.order_number) = upper($2)
        """,
        conversation_id,
        number.strip(),
    )
    return dict(row) if row else None


async def open_return(conversation_id: str, number: str, item_id: int, reason: str) -> dict | None:
    """Open a return for one item, if the order belongs to this customer.

    The ownership check is part of the INSERT rather than a lookup
    beforehand, so there is no gap between checking and acting.
    """
    row = await pool().fetchrow(
        """
        INSERT INTO returns (id, order_number, item_id, reason)
        SELECT gen_random_uuid(), o.number, i.id, $4
        FROM orders o
        JOIN order_items i ON i.order_number = o.number
        JOIN conversations v ON v.verified_customer_id = o.customer_id
        WHERE v.id = $1 AND upper(o.number) = upper($2) AND i.id = $3
        RETURNING id, order_number, item_id, status, opened_at
        """,
        conversation_id,
        number.strip(),
        item_id,
        reason,
    )
    return {**dict(row), "id": str(row["id"])} if row else None
