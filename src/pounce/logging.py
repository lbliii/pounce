"""
Logging configuration and access log formatting.

Configures stdlib logging with pounce-specific formatting. Provides both
text and JSON access log output for request/response metrics.

Text format:
    {timestamp} {level} {client} - "{method} {path} HTTP/{version}" {status} {bytes} {duration}ms

JSON format:
    {"timestamp": "...", "level": "INFO", "logger": "pounce.access", "method": "GET", ...}

"""

import json as json_module
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from pounce.config import ServerConfig

# Access logger — separate from the general logger for filtering
access_logger = logging.getLogger("pounce.access")
logger = logging.getLogger("pounce")

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Module-level flag for structured JSON logging
_json_logging: bool = False


class _JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production observability."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json_module.dumps(entry, default=str)


def configure_logging(config: ServerConfig) -> None:
    """Configure stdlib logging for pounce and framework loggers.

    Sets up the pounce logger hierarchy and the chirp framework logger
    with the configured log level and a stream handler to stderr.
    Both loggers share the same formatting and output destination.

    When ``config.log_format`` is ``"json"``, uses structured JSON output.

    Args:
        config: Server configuration with log_level and log_format settings.

    """
    global _json_logging  # noqa: PLW0603
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    _json_logging = config.log_format.lower() == "json"

    formatter: logging.Formatter
    if _json_logging:
        formatter = _JSONFormatter()
    else:
        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Configure root pounce logger
    root = logging.getLogger("pounce")
    root.setLevel(level)

    # Only add handler if none exist (avoid duplicates on reconfigure)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # Configure chirp framework logger alongside pounce
    chirp_logger = logging.getLogger("chirp")
    chirp_logger.setLevel(level)
    if not chirp_logger.handlers:
        chirp_handler = logging.StreamHandler(sys.stderr)
        chirp_handler.setLevel(level)
        chirp_handler.setFormatter(formatter)
        chirp_logger.addHandler(chirp_handler)


def access_log(
    method: str,
    path: str,
    status: int,
    bytes_sent: int,
    duration_ms: float,
    client: str,
    *,
    http_version: str = "1.1",
) -> None:
    """Log an access log entry for a completed request.

    When JSON logging is enabled, emits a structured JSON object
    with all fields as top-level keys. Otherwise, uses the standard
    combined log format.

    Args:
        method: HTTP method (e.g., "GET").
        path: Request path (e.g., "/api/users").
        status: HTTP response status code.
        bytes_sent: Number of response body bytes sent.
        duration_ms: Request duration in milliseconds.
        client: Client address string (e.g., "127.0.0.1:5000").
        http_version: Protocol version string (e.g., "1.1", "2").

    """
    level = logging.WARNING if status >= 500 else logging.INFO

    if _json_logging:
        entry = json_module.dumps({
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "level": logging.getLevelName(level),
            "logger": "pounce.access",
            "method": method,
            "path": path,
            "http_version": http_version,
            "status": status,
            "bytes_sent": bytes_sent,
            "duration_ms": round(duration_ms, 1),
            "client": client,
        })
        access_logger.log(level, "%s", entry)
    else:
        access_logger.log(
            level,
            '%s - "%s %s HTTP/%s" %d %d %.1fms',
            client,
            method,
            path,
            http_version,
            status,
            bytes_sent,
            duration_ms,
        )
