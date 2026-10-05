"""Vercel entrypoint for the Telegram policy review console.

The application already exposes a ``BaseHTTPRequestHandler`` for the local
stdlib server. Vercel's Python runtime supports the same handler type, so this
small adapter keeps local and hosted routing on the same code path.

Vercel statically scans every ``api/*.py`` file *before* it builds anything and
only treats a file as a Serverless Function when it finds a top-level ``app``,
``application``, or ``handler`` name (``handler`` must be a class inheriting
from ``BaseHTTPRequestHandler``). An import alias such as
``from app import Handler as handler`` is invisible to that scan, so the file
was skipped and the build failed with:

    Error: The pattern "api/index.py" defined in `functions` doesn't match any
    Serverless Functions inside the `api` directory.

Declaring a real subclass below satisfies both the scanner and the runtime
without duplicating any routing logic. Do not "simplify" it back into an
import alias.

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

from app import Handler as _Handler  # noqa: E402  (configure env before import)
from app import init_db  # noqa: E402


# Initialize the schema once per warm function instance. The call is safe on
# subsequent invocations and also seeds the optional demo workspace.
init_db()


class handler(_Handler):  # noqa: N801 - Vercel requires this exact lower-case name
    """The Vercel Function handler: every route inherited from ``app.Handler``.

    Vercel mixes this class with its own request handler at runtime
    (``issubclass(handler, BaseHTTPRequestHandler)`` is all it checks), so the
    body stays empty on purpose.
    """
