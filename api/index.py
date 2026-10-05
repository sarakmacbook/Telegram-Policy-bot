"""Vercel entrypoint for the Telegram policy review console.

The application already exposes a ``BaseHTTPRequestHandler`` for the local
stdlib server. Vercel's Python runtime supports the same handler type, so this
small adapter keeps local and hosted routing on the same code path.

Vercel Functions have an ephemeral writable filesystem. Keep the SQLite/media
fallback in /tmp so the function can start; use the deployment notes in the
README before connecting a real bot.
"""
from __future__ import annotations

import os

# The project defaults to ./data for a local process, but the deployed bundle
# is read-only. /tmp is writable for the lifetime of a warm function instance.
# Always override DATA_DIR on Vercel so a copied local .env cannot make startup
# fail by pointing SQLite at the read-only deployment bundle.
if os.environ.get("VERCEL"):
    os.environ["DATA_DIR"] = "/tmp/telegram-policy-bot"
else:
    os.environ.setdefault("DATA_DIR", "/tmp/telegram-policy-bot")
os.environ.setdefault("APP_ENV", "production")

from app import Handler as handler  # noqa: E402  (configure env before import)
from app import init_db  # noqa: E402


# Initialize the schema once per warm function instance. The call is safe on
# subsequent invocations and also seeds the optional demo workspace.
init_db()
