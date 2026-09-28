# Northwind Support Agent

A customer support chat assistant for Northwind Tools, a fictional online
woodworking equipment retailer. It answers questions about returns,
warranties, shipping, order changes and price matching, strictly according
to company policy — and declines to invent exceptions.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then add your NVIDIA API key

docker compose up -d        # Postgres on port 5433

npm --prefix web install    # once
npm --prefix web run build  # builds the client into static/

uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. An interactive API explorer is available at
<http://127.0.0.1:8000/docs>.

Conventions for this repository — where files belong, how commits are
shaped, the house style for comments — are in [CLAUDE.md](CLAUDE.md).

## Project structure

| Path | Responsibility |
|---|---|
| `app/main.py` | Application setup: lifespan, middleware. |
| `app/routes.py` | The HTTP endpoints. |
| `app/config.py` | Every setting read from the environment, in one place. |
| `app/llm.py` | Model client. Sole point of contact with the inference provider. |
| `app/store.py` | Database access. Sole point of contact with Postgres. |
| `app/prompts.py` | The support policy that governs every answer. |
| `app/sse.py` | Server-Sent Events formatting. |
| `migrations/` | Numbered SQL files, applied once each at startup. |
| `web/` | The React client. Built into `static/`, which is not committed. |
| `tests/` | Tests. No API key or network required. |

## How it works

A request carries the full conversation to `POST /api/chat`. The server
prepends the support policy as a system message and streams the reply back
as Server-Sent Events, forwarding each piece as the model writes it.

Three kinds of event travel over that stream:

| Event | When | Shown as |
|---|---|---|
| `thinking` | While the model reasons, before it answers | A collapsed toggle above the reply |
| `text` | The reply itself | Markdown, rendered to HTML |
| `usage` | Once, at the end | Token counts beneath the reply |

The model reasons before answering, which can take several seconds on a
complex question. Surfacing that as a `thinking` indicator means the user
sees activity within a second rather than an empty bubble.

Replies are markdown, so the client renders them through `marked` and then
sanitises the result with `DOMPurify`. The sanitising step is required, not
cosmetic: a customer can write anything into the chat, the model can be
induced to repeat it, and unsanitised model output reaching `innerHTML`
would execute it.

The model is stateless — it retains nothing between requests — so the entire
conversation is resent on every call.

The policy lives in its own module, away from anything that varies. Providers
charge less for a prefix they have seen before, but only when it is identical
byte for byte; a timestamp or customer name inserted into that text would
silently forfeit the discount.

## Conversations and identity

Conversations are stored in Postgres. The browser keeps only the conversation
id, and the server loads the messages, so a refresh restores the chat.

Every visitor gets a signed session cookie holding a random identifier. It
cannot be forged — altering the value breaks the signature. Conversations
belong to that identifier, and a request for someone else's conversation
returns **404 rather than 403**: a 403 would confirm the conversation exists,
which allows probing for valid ids.

There are no accounts yet. `owner_id` holds a session identifier today and a
user identifier once login exists, so no schema change is needed then. Until
that happens, clearing cookies makes a conversation unreachable, and a
different browser is a different visitor.

Token counts are stored per message, so the cost of a conversation is a query
rather than an estimate.

## Database

| Where | Database |
|---|---|
| Local | Postgres 16 via `docker compose up -d`, on port 5433 |
| CI | A Postgres service container started by GitHub Actions |
| Production | Any hosted Postgres; only `DATABASE_URL` changes |

Schema changes go in a new numbered file under `migrations/`. They are applied
in order at startup and recorded, so each runs exactly once. A migration is
never edited after it has run anywhere — the database cannot notice the
change, so the file would no longer describe what is deployed.

## Configuration

Everything is read in `app/config.py`, and a missing required setting stops
the process at startup with a message naming the fix.

| Setting | Required | Default |
|---|---|---|
| `NVIDIA_API_KEY` | yes | — |
| `DATABASE_URL` | yes | — |
| `SECRET_KEY` | yes | signs the session cookie |
| `MODEL` | no | `nvidia/nemotron-3-super-120b-a12b` |
| `REQUEST_TIMEOUT` | no | 60s |
| `MAX_REPLY_TOKENS` | no | 1024 |
| `DATABASE_POOL_SIZE` | no | 10 |
| `COOKIE_HTTPS_ONLY` | no | `false` — set `true` in production |

`SECRET_KEY` must stay stable across restarts. Changing it invalidates every
existing session, which on a deploy means every visitor loses their
conversation.

The provider returns intermittent 5xx responses, so requests are retried up
to four times with exponential backoff and jitter. Client errors — bad
request, bad key, unknown model — are not retried, since the same request
would fail identically.

## Working on the client

Two servers, so the client gets hot reload:

```bash
uvicorn app.main:app --reload   # terminal one — the API, on :8000
npm --prefix web run dev        # terminal two — the client, on :5173
```

Open <http://localhost:5173> — `localhost`, not `127.0.0.1`, since Vite
binds to IPv6 and the numeric address will not reach it. Vite forwards
anything under `/api` to the Python server, so the browser sees a single
origin and there is no CORS to configure.

In production there is only one server: `npm run build` writes the client
into `static/`, and FastAPI serves it.

```
web/src/
  shared/              api, types and markdown — used by more than one thing
  hooks/useChat/       conversation state and streaming
  components/<Name>/   one folder per component, tests beside it
```

## Development

```bash
pip install -r requirements-dev.txt
ruff check .                      # lint
ruff format .                     # format
pytest -q --cov                   # tests, and the 100% coverage gate

npm --prefix web run lint         # Biome: lint and formatting
npm --prefix web run format       # Biome, writing fixes
npm --prefix web run test:coverage
npm --prefix web run build        # type-checks as it builds
```

**Coverage must be 100% on both halves**, and the thresholds live in
`pyproject.toml` and `vite.config.ts` rather than in the workflows — so the
commands above enforce exactly what CI does.

That is not a claim of correctness; code can be fully covered and still
wrong. It means untested code cannot arrive unnoticed, and that anything
unreachable has to be dealt with rather than ignored.

Tests stub the model call, so they need no API key, make no network requests
and cost nothing. They do use a real Postgres rather than a stand-in, since
that is the only way to catch what differs between databases.

They run against `<your database>_test`, created automatically on first run.
The suite drops tables to prove the migrations work and empties them between
tests, so it must never be pointed at a database you are developing against —
the `_test` suffix is applied unconditionally for that reason.

CI is split so a change only runs the checks it can affect:

| Workflow | Runs when | Does |
|---|---|---|
| Backend | `app/`, `tests/`, `migrations/`, requirements change | ruff lint and format check, pytest at 100% coverage against a real Postgres, a startup check |
| Frontend | `web/` changes | Biome lint and format, Vitest at 100% coverage, then a build — which type-checks |
| CodeQL | **always** | security analysis |

Formatting is checked rather than applied. CI quietly reformatting would
leave the branch different from what was reviewed.

Each half posts its coverage table as a comment on the pull request, editing
its own previous comment rather than adding another on every push. The full
browsable report is attached to the run as an artifact.

CodeQL is deliberately unfiltered. A merge requires a code scanning result,
and a workflow skipped by a path filter never reports one — so filtering it
would block any pull request that happened to miss the filter.

## Known limitations

- **No live data access.** The agent can quote policy but cannot look up an
  order or check a shipment.
- **No per-user rate limiting or spend controls.**

## Verifying behaviour

| Ask | Expected |
|---|---|
| What's the restocking fee on a used power tool? | 15% |
| Do you ship solvents to Hawaii? | No |
| Can I return opened sandpaper? | No — consumables are non-returnable |
| Can you make an exception just this once? | Declines, offers a valid alternative |

The last case matters most. The policy forbids inventing exceptions, and the
assistant is expected to hold that line under pressure while still being
helpful.

## License

MIT — see [LICENSE](LICENSE).
