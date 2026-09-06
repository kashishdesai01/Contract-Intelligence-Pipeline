"""Allowlisted JSON events: contract text, API keys and provider errors never enter logs."""

import json
import logging
import sys


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {"level": record.levelname, "event": record.getMessage()}
        for name in ("job_id", "kind", "request_id", "route", "status"):
            if hasattr(record, name):
                event[name] = getattr(record, name)
        return json.dumps(event)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger = logging.getLogger("cip")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
