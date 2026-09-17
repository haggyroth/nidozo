"""Middleware configuration for the Nidozo FastAPI app."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# The allowlist lives in origin.py — the same list gates the WebSocket
# handshakes, which CORS middleware never sees (#280).
from nidozo.api.origin import CORS_ORIGINS

__all__ = ["CORS_ORIGINS", "add_cors"]


def add_cors(app: FastAPI) -> None:
    """Attach CORS middleware to *app*."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
