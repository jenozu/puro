"""Isolate every test from the operator's real environment and .env.

On a workstation or VPS the shell may export DEFAULT_SENDER_*, PURO_* or
PUROLATOR_* values and a real .env may exist. Tests must behave exactly as in CI,
which has neither, and must never read real credentials.
"""
import os

import pytest

_PREFIXES = ("PURO", "DEFAULT_SENDER_")


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("puro.config.load_dotenv", lambda *a, **k: False)
