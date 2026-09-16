"""Shared pytest configuration.

Auth is fail-closed at startup: ``create_app()`` raises unless
``NIDOZO_API_TOKEN`` is set or ``NIDOZO_ALLOW_INSECURE=1``. The test suite opts
into the open mode globally; individual auth tests override by setting or
clearing these env vars themselves.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _allow_insecure_auth() -> None:
    os.environ["NIDOZO_ALLOW_INSECURE"] = "1"
