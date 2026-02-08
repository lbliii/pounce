"""
Logging configuration and access log formatting.

Configures stdlib logging with pounce-specific formatting. Provides a
structured access log function for request/response metrics.

Format:
    {timestamp} {level} {client} - "{method} {path} HTTP/{version}" {status} {bytes} {duration}ms

"""

import logging
import sys

from pounce.config import ServerConfig

# Access logger — separate from the general logger for filtering
access_logger = logging.getLogger("pounce.access")
logger = logging.getLogger("pounce")

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def configure_logging(config: ServerConfig) -> None:
    """Configure stdlib logging for pounce.

    Sets up the root pounce logger with the configured log level and
    a stream handler to stderr.

    Args:
        config: Server configuration with log_level setting.

    """
    level = getattr(logging, config.log_level.upper(), logging.INFO)

    # Configure root pounce logger
    root = logging.getLogger("pounce")
    root.setLevel(level)

    # Only add handler if none exist (avoid duplicates on reconfigure)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        handler.setFormatter(formatter)
        root.addHandler(handler)

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

    Args:
        method: HTTP method (e.g., "GET").
        path: Request path (e.g., "/api/users").
        status: HTTP response status code.
        bytes_sent: Number of response body bytes sent.
        duration_ms: Request duration in milliseconds.
        client: Client address string (e.g., "127.0.0.1:5000").
        http_version: Protocol version string (e.g., "1.1", "2").

    """
    access_logger.info(
        '%s - "%s %s HTTP/%s" %d %d %.1fms',
        client,
        method,
        path,
        http_version,
        status,
        bytes_sent,
        duration_ms,
    )
