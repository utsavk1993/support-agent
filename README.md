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

uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. An interactive API explorer is available at
<http://127.0.0.1:8000/docs>.

## Project structure

| Path | Responsibility |
|---|---|
| `main.py` | HTTP server. Serves the client and handles chat requests. |
| `llm.py` | Model client. Sole point of contact with the inference provider. |
| `prompts.py` | The support policy that governs every answer. |
| `static/index.html` | Browser client. |
| `tests/` | Smoke tests. No API key or network required. |

## How it works

A request carries the full conversation to `POST /api/chat`. The server
prepends the support policy as a system message, sends everything to the
model, and returns the reply along with token usage.

The model is stateless — it retains nothing between requests — so the entire
conversation is resent on every call.

The policy lives in its own module, away from anything that varies. Providers
charge less for a prefix they have seen before, but only when it is identical
byte for byte; a timestamp or customer name inserted into that text would
silently forfeit the discount.

## Configuration

| Setting | Where | Default |
|---|---|---|
| Model | `llm.py` | `nvidia/nemotron-3-super-120b-a12b` |
| Request timeout | `llm.py` | 60s |
| API key | `.env` | — |

The provider returns intermittent 5xx responses, so requests are retried up
to four times with exponential backoff and jitter. Client errors — bad
request, bad key, unknown model — are not retried, since the same request
would fail identically.

## Development

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

Tests stub the model call, so they need no API key, make no network requests,
and cost nothing. CI runs lint, tests, and a startup check on every pull
request.

## Known limitations

- **Conversations are not persisted.** History is held client-side and lost
  on refresh.
- **Responses are not streamed.** The client waits for the complete reply.
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
