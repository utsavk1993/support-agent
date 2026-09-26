"""
config.py — everything the application reads from its environment.

WHY THIS EXISTS
---------------
These values used to be read in three different files, each failing its own
way when something was missing: one raised at import, one raised on the first
request, one silently fell back to a development default. There was no single
place that said what the application needs in order to run.

Now there is. Import a name from here and it is either correct or the process
has already stopped with an explanation.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# The project root — this file's directory, then up one.
ROOT = Path(__file__).parent.parent

# Read .env if present. It does NOT overwrite variables already set, so real
# environment variables always win. That is what makes the same code work in
# development, where settings come from a file, and in production, where they
# come from the platform.
load_dotenv(ROOT / ".env")


def _required(name: str, hint: str) -> str:
    """Read a setting the application cannot run without.

    Failing here, at startup, is deliberate. The alternative is a server that
    starts happily and breaks on the first real request, usually with an
    error that says nothing about the actual cause.
    """
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. {hint}")
    return value


# --- the model ------------------------------------------------------------
NVIDIA_API_KEY = _required(
    "NVIDIA_API_KEY",
    "Copy .env.example to .env and add your key from https://build.nvidia.com",
)

NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# Which model to use. One line, one place.
MODEL = os.getenv("MODEL", "nvidia/nemotron-3-super-120b-a12b")

# Give up on a request that gets no answer. Without a limit, a request that
# never returns hangs its caller forever — no error, no crash, just stuck.
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))

# The longest reply we will accept. A safety belt: without it, a confused
# model can ramble for thousands of tokens and bill you for all of them.
MAX_REPLY_TOKENS = int(os.getenv("MAX_REPLY_TOKENS", "1024"))


# --- the database ---------------------------------------------------------
DATABASE_URL = _required(
    "DATABASE_URL",
    "Start Postgres and set the connection string. See the README.",
)

# Small on purpose. Every connection costs memory on the database server, and
# a connection is held only for the length of a query here — microseconds —
# not for the length of a request.
DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "10"))


# --- sessions -------------------------------------------------------------
# Signs the cookie that identifies a visitor. It must stay the same across
# restarts: change it and every existing session is invalidated, which on a
# deploy means every visitor loses their conversation.
SECRET_KEY = _required(
    "SECRET_KEY",
    "Set it to a long random value. See .env.example.",
)

# Send the session cookie only over HTTPS. Off in development, where there
# is no certificate; on everywhere else.
COOKIE_HTTPS_ONLY = os.getenv("COOKIE_HTTPS_ONLY", "false").lower() == "true"


# --- where the built client lives -----------------------------------------
STATIC_DIR = ROOT / "static"
