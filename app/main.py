"""
main.py — building the application.

Wires together the pieces and hands FastAPI something to run. The endpoints
themselves live in routes.py; this file is about the machinery around them —
what opens at startup, what closes at shutdown, what wraps every request.

RUN IT WITH:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles  # Serves the built client.
from starlette.middleware.sessions import SessionMiddleware  # Signed cookies.

from app import config, store
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the database when the server starts, close it when it stops.

    Everything before `yield` runs at startup, everything after at shutdown.
    The connection pool is opened once and shared, because establishing a
    connection is slow and Postgres limits how many can exist at once.
    """
    await store.connect()
    yield
    await store.disconnect()


def create_app() -> FastAPI:
    """Build the application.

    A function rather than a module-level object so a test can construct a
    fresh one, and so the order of setup is something you can read top to
    bottom rather than infer from where statements happen to sit.
    """
    app = FastAPI(title="Northwind Support Agent", lifespan=lifespan)

    # Gives every visitor a cookie holding a random identifier, signed with
    # our secret. The browser can read it but cannot change it: altering the
    # value breaks the signature and the server rejects it. That is what
    # makes "this conversation is not yours" enforceable rather than polite.
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SECRET_KEY,
        same_site="lax",      # not sent on cross-site requests, which blocks CSRF
        https_only=config.COOKIE_HTTPS_ONLY,
    )

    app.include_router(router)

    # Serve the compiled client. Vite builds JavaScript and CSS into
    # static/assets with hashed filenames, which lets them be cached
    # forever — a new build produces new names.
    #
    # Only mounted if the directory exists. It is not committed, so a fresh
    # checkout has no client until `npm run build` has run, and the API
    # should still start and work in that state.
    assets = config.STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    return app


app = create_app()
