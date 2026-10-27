"""Allowlisted, redacted events only. Never write carrier payloads or addresses."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from .config import AppConfig, redact_text


_ALLOWED = {"event", "status", "environment", "order_count", "error_code", "attempt_id"}
_LOGGER = logging.getLogger("puro")


def configure_logging(level: str = "INFO") -> None:
    """Configure only Puro's logger (avoid altering host/application loggers)."""
    _LOGGER.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not _LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        _LOGGER.addHandler(handler)
    _LOGGER.propagate = False


def log_event(name: str, config: AppConfig, **fields: Any) -> None:
    """Output a small allowlisted JSON event, no exceptions/raw SOAP/customer data."""
    event = {"timestamp": datetime.now(timezone.utc).isoformat(),
             "event": re.sub(r"[^a-z0-9_]", "_", name.lower())[:64]}
    for key, value in fields.items():
        if key not in _ALLOWED or key == "event":
            continue
        if isinstance(value, (int, float, bool)):
            event[key] = value
        elif isinstance(value, str):
            cleaned = redact_text(value, config)
            # Avoid free-form log inputs; mask everything except short status tokens.
            if re.fullmatch(r"[a-zA-Z0-9_\-]{1,64}", cleaned):
                event[key] = cleaned
    _LOGGER.info(json.dumps(event, sort_keys=True))
