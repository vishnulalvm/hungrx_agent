import logging
import sys
from datetime import datetime, timezone
from typing import Any

from core.config.settings import Settings


class JsonFormatter(logging.Formatter):
    """Renders each log record as a single JSON line.

    Deliberately dependency-free (no python-json-logger) so both the api
    and worker processes can share it via core without pulling in extra
    packages for something this small.
    """

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in ("request_id", "path", "method", "status_code", "duration_ms", "user_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    """Configures the root logger once at process startup.

    JSON structured output in production/staging (log aggregator friendly);
    plain human-readable output in development.
    """
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    if settings.is_production or settings.environment == "staging":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root.handlers = [handler]

    # Quiet noisy third-party loggers down to warnings by default.
    for noisy_logger in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
