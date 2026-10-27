"""Config/logging tests use fictional values only."""
import json
import logging

from puro.auditlog import configure_logging, log_event
from puro.config import AppConfig


def test_only_allowlisted_redacted_events(caplog):
    config = AppConfig(api_key="EXAMPLESECRETNOTREAL",
                       api_password="EXAMPLEPASSWORDNOTREAL",
                       environment="development")
    configure_logging()
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("puro")
    handler = Capture()
    logger.addHandler(handler)
    try:
        log_event("import_complete", config, status="success", order_count=2,
                  address="12 Fictional Lane", error_code="EXAMPLESECRETNOTREAL")
        assert len(records) == 1
        event = json.loads(records[0])
        assert event["event"] == "import_complete"
        assert event["order_count"] == 2
        assert event["status"] == "success"
        assert "address" not in event
        assert "error_code" not in event
        assert "EXAMPLESECRETNOTREAL" not in records[0]
    finally:
        logger.removeHandler(handler)
