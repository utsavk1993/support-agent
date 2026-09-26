# Conventions

How this repository is organised, and why. Read before adding a file.

Where a rule is a target the code does not yet meet, it says so. Do not
assume something is settled because it appears here — check the
**Not yet conforming** section at the end.

---

## What this is

A customer support assistant for Northwind Tools, a fictional woodworking
retailer. A Python API streams replies from a language model and stores
conversations in Postgres; a React client talks to it.

---

## Layout

```
app/          the Python application
migrations/   numbered SQL, applied once each at startup
tests/        Python tests
web/          the React client
static/       the built client — generated, not committed
```

---

## The backend

**One module, one responsibility.** Each of these is the *only* place its
concern is handled, and that is the point:

| Module | Owns |
|---|---|
| `app/main.py` | building the application: lifespan, middleware |
| `app/routes.py` | the HTTP endpoints |
| `app/config.py` | every value read from the environment |
| `app/llm.py` | all contact with the model provider |
| `app/store.py` | all SQL |
| `app/prompts.py` | the support policy |
| `app/sse.py` | Server-Sent Events formatting |

**Nothing outside `store.py` writes SQL. Nothing outside `llm.py` talks to
the provider. Nothing outside `config.py` reads an environment variable.**
Changing a database, a model or a deployment target should touch one file.

**No ORM.** The queries are short enough to read, and seeing the SQL matters
more here than the convenience of hiding it. An ORM also makes the N+1
problem easy to cause by accident. Revisit this past roughly ten tables.

**Settings fail at startup, not at first use.** `config.py` raises for a
missing required value with a message naming the fix. A server that starts
happily and breaks on the first real request is worse than one that refuses
to start.

**A leading underscore means internal.** `_with_retry`, `_migrate`, `_pool`
are not called from outside their module. Python does not enforce this; it
tells the next person what is safe to change.

**Never hold a database connection across a model call.** Borrow for the
query, release, call the model, borrow again. A connection held for the life
of a request — which is what wrapping a handler in a transaction does —
exhausts the pool at a fraction of the load. This is the single most
expensive mistake available in this codebase.

**Nothing in an async path may block.** `time.sleep`, synchronous database
drivers and blocking HTTP calls freeze *every* in-flight request, not only
their own. Use `await asyncio.sleep`, `asyncpg`, `AsyncOpenAI`.

---

## The client

### Components

One folder per component, holding everything that belongs to it:

```
web/src/components/MessageBubble/
  MessageBubble.tsx          the component
  MessageBubble.test.tsx     its tests
  MessageBubble.module.css   its styles, if it has any
  strings.ts                 its text, if it has any
```

A component's styles, strings and tests live beside it, so deleting the
folder deletes the component completely and nothing is left orphaned.

### Hooks

The same shape:

```
web/src/hooks/useChat/
  useChat.ts
  useChat.test.ts
```

### Shared

Anything used by more than one component or hook:

```
web/src/shared/
  markdown.ts   markdown to HTML, via a sanitiser
  api.ts        HTTP and event-stream parsing
  types.ts      the shapes that cross the wire
```

If something in `shared/` is only used once, it belongs in that component's
folder instead. Things move *into* `shared/` when a second caller appears,
not in anticipation of one.

### Other rules

**Components draw. Hooks hold state. `shared/api.ts` talks to the server.**
A component that parses a stream, or a hook that builds DOM, is in the wrong
place. The previous client did all three in one function, which is why this
rule exists.

**Model output is never trusted.** It reaches the page only through
`shared/markdown.ts`, which sanitises. A customer can write anything into
the chat and the model can be induced to repeat it, so unsanitised output
reaching `innerHTML` would execute. Never write your own sanitiser.

**Stream events are a discriminated union.** `types.ts` describes each shape
so the compiler checks the tag and the fields together. String comparisons
against event types fail silently at runtime; this does not.

---

## Tests

**Python.** `pytest`, in `tests/`, against a **real Postgres** — never a
stand-in, since the point is to catch what differs between databases. The
database name always takes a `_test` suffix, applied unconditionally in
`conftest.py`: the suite drops tables and truncates between tests, and
pointing it at a development database destroys real data. That has happened.

The model is always stubbed. Tests make no network calls and cost nothing.

**A note on event loops.** `asyncpg` binds a connection to the loop that
created it, `asyncio.run()` makes a fresh loop per call, and `TestClient`
runs the app on its own. Tests open a short-lived connection per query
rather than borrowing the application's pool. Getting this wrong produces
`another operation is in progress`.

**TypeScript.** Tests sit beside the thing they test, named `*.test.tsx` or
`*.test.ts`.

**What to test.** Behaviour that would be embarrassing to break: one person
reading another's conversation, a reply lost when a stream fails, a token
breakdown that does not add up. Not implementation detail.

---

## The database

**Schema changes go in a new numbered migration.** Never edit one that has
run anywhere: the database records only *that* it ran, so an edited file
stops describing what is deployed.

```
migrations/001_conversations.sql
migrations/002_add_summary.sql
```

They are applied in order at startup and recorded, so each runs exactly
once and every environment matches.

**`CREATE` and `ADD COLUMN` fail safely if run twice. `DROP`, `TRUNCATE`
and `DELETE` do not.** A migration containing the latter deserves a backup
taken first.

---

## Commits and pull requests

**One commit per issue.** After the first commit on a branch, every further
change is:

```bash
git commit --amend --no-edit
git push --force-with-lease
```

Never a second commit. This makes the merge method irrelevant — follow-up
commits have leaked onto `main` before.

**`--force-with-lease`, never `--force`.** It refuses if someone else has
pushed meanwhile.

**Do not stack pull requests.** A branch based on another branch is merged
into a base that may already be spent, and GitHub marks it merged while none
of its changes reach `main`. That has happened here. Branch from `main`.

**Commit messages wrap at ~72 characters.** `git log` does not reflow.
**Pull request and issue bodies do not wrap** — GitHub renders a single
newline as a line break, so hard wrapping produces ragged short lines.

**Every pull request closes an issue.** `Closes #n`.

---

## Sprints

Two-week milestones, Sunday to Saturday, named after woodworking joints, with
the range in the title:

```
Dovetail · 20 Sep – 3 Oct 2026
```

**A milestone has no description while it is open.** What gets built is not
known in advance. At the end of a sprint, summarise the pull requests that
actually landed and write that into the description.

Assign every issue and pull request to the open sprint.

---

## CI

Three workflows, split so a change only runs the checks it can affect:

| Workflow | Runs when | Does |
|---|---|---|
| `backend.yml` | `app/`, `tests/`, `migrations/`, requirements change | ruff, pytest against a real Postgres, a startup check |
| `frontend.yml` | `web/` changes | lint, then build — which type-checks |
| `codeql.yml` | **always** | security analysis |

**CodeQL is deliberately unfiltered.** The branch ruleset requires a code
scanning result, and a workflow skipped by a path filter never reports one,
so filtering it would block any pull request that missed the filter.

**Adding a CodeQL language cannot be merged through a normal pull request.**
Code scanning compares the head against the base, `main` has no baseline for
the new configuration, and the check returns `1 configuration not found` —
which the ruleset treats as unsatisfied. Since the ruleset also forbids
pushing straight to `main`, the only ways through are an administrator
bypass for that one merge, or relaxing the code scanning rule briefly while
it lands. Attempted twice and abandoned both times.

---

## Writing comments

The house style, and it is not the usual one.

**Explain why, never what.** `# increment the counter` above `count += 1` is
noise. Why the counter exists is not.

**Plain language over jargon.** Write "the server had a bad moment", not
"a transient upstream blip". If a term has to be used, explain it once in
the place it first appears.

**Comment the surprising, not the routine.** The line that deserves five
lines of explanation is the one that looks wrong until you know why — 
`await asyncio.sleep` rather than `time.sleep`, `404` rather than `403`,
saving the question before calling the model.

**Never reference how the code came to be.** No "step 3", no "we will fix
this later", no narration of the process. Comments describe the software.

**Python:** two spaces before an inline `#`. PEP 8, enforced by `ruff`.

---

## Not yet conforming

Honest list of where the code does not match the above.

- **The client does not follow the component layout.** Components sit flat
  in `components/`, hooks flat in `hooks/`, and `markdown.ts`, `api.ts` and
  `types.ts` sit at `src/` root rather than in `shared/`.
- **There are no frontend tests, and no test runner installed.** The
  frontend CI job lints and builds only.
- **CodeQL scans Python only.** TypeScript scanning is worth adding — the
  client renders model output as HTML — but it cannot be merged normally;
  see the note under CI.
- **`main` carries three loose commits** from an early pull request that was
  merged without squashing. Left as it is; the rule above prevents a repeat.
