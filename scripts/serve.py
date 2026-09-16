"""
Start the Nidozo API server.

Requires the local Showdown server to be running:
    ./scripts/start_showdown.sh

Usage:
    uv run python scripts/serve.py
    uv run python scripts/serve.py --port 8080 --db path/to/nidozo.db
    uv run python scripts/serve.py --reload              # dev hot-reload
    uv run python scripts/serve.py --log-level DEBUG     # verbose JSON logs

Notes:
    --reload uses uvicorn's import-string mode.  The --db flag is ignored when
    --reload is active; set NIDOZO_DB in your environment instead.
    create_app() is called as a factory so each reload picks up code changes.

    Logs are emitted as newline-delimited JSON to stdout.  Set --log-level to
    DEBUG for verbose output during development.
"""

import argparse
import os
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the Nidozo API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO, or LOG_LEVEL env var).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Hot-reload on code changes (dev only). Uses NIDOZO_DB env var; --db is ignored.",
    )
    args = parser.parse_args()

    # Loopback-only bind is the documented safe case: keep local dev frictionless
    # by opting out of the fail-closed guard automatically. A non-loopback host
    # (e.g. `--host 0.0.0.0`) still requires NIDOZO_API_TOKEN.
    if (
        not os.environ.get("NIDOZO_API_TOKEN")
        and args.host in ("127.0.0.1", "localhost", "::1")
    ):
        os.environ.setdefault("NIDOZO_ALLOW_INSECURE", "1")

    # Configure structured JSON logging before anything else starts.
    from nidozo.api.logging_config import configure_logging
    configure_logging(level=args.log_level)

    # NIDOZO_DB preferred; NIMZO_DB accepted for backward compat
    db_env = os.environ.get("NIDOZO_DB") or os.environ.get("NIMZO_DB", "nidozo.db")
    db_path = Path(args.db) if args.db else Path(db_env)

    if args.reload:
        # uvicorn reload requires an import string (not an app instance).
        # Propagate the resolved DB path via env var so create_app() picks it up.
        if args.db:
            import warnings
            warnings.warn(
                "--db is ignored in --reload mode; set NIDOZO_DB env var instead.",
                stacklevel=1,
            )
        os.environ.setdefault("NIDOZO_DB", str(db_path))
        uvicorn.run(
            "nidozo.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
            log_config=None,   # suppress uvicorn's default log config; ours is already set
        )
    else:
        from nidozo.api.app import create_app
        app = create_app(db_path=db_path)
        uvicorn.run(app, host=args.host, port=args.port, log_config=None)
