"""
Logging configuration and access log formatting.

Configures stdlib logging with pounce-specific formatting. Provides text,
JSON, and pretty (TTY) access log output for request/response metrics.

Format modes:
    auto   — pretty on TTY, JSON when piped (default)
    text   — classic combined-log format via stdlib logging
    json   — flat structured JSON written directly to stderr
    pretty — colored compact lines on TTY (resolved from auto)

"""

import json as json_module
import logging
import sys
import threading
from datetime import UTC, datetime
from typing import Final, TypedDict

from pounce.config import ServerConfig

# Access logger — separate from the general logger for filtering
access_logger = logging.getLogger("pounce.access")
logger = logging.getLogger("pounce")

_LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
_DATE_FORMAT: Final = "%Y-%m-%d %H:%M:%S"

# Module-level state set by configure_logging()
_json_logging: bool = False
_resolved_format: str = "text"  # "text", "json", or "pretty"

# Thread-safe lock for direct stderr writes (JSON and pretty modes).
# Essential for free-threaded Python 3.14t where the GIL is disabled.
_stderr_lock = threading.Lock()

# ANSI color constants for pretty mode
_RESET: Final = "\033[0m"
_GREEN: Final = "\033[32m"
_YELLOW: Final = "\033[33m"
_RED: Final = "\033[31m"
_DIM: Final = "\033[2m"
_BOLD: Final = "\033[1m"


def _human_bytes(n: int) -> str:
    """Format byte count for human readability."""
    if n < 1000:
        return f"{n}B"
    if n < 1_000_000:
        return f"{n / 1000:.1f}kB"
    return f"{n / 1_000_000:.1f}MB"


def _status_color(status: int) -> str:
    """Return ANSI color code for an HTTP status code."""
    if status < 300:
        return _GREEN
    if status < 400:
        return _DIM
    if status < 500:
        return _YELLOW
    return _RED


def _duration_str(ms: float) -> str:
    """Format duration in human-readable form."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


class LogEntry(TypedDict, total=False):
    """Structured log entry for JSON output."""

    ts: str
    level: str
    logger: str
    message: str
    exception: str


class _JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production observability."""

    def format(self, record: logging.LogRecord) -> str:
        entry: LogEntry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json_module.dumps(entry, default=str)


class _PrettyFormatter(logging.Formatter):
    """Compact colored formatter for TTY output — renders through kida templates."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            msg += "\n" + self.formatException(record.exc_info)
        try:
            from pounce._output import _render

            return _render("log_line.kida", ts=ts, message=msg, level=record.levelname.lower())
        except Exception:
            # Fallback to plain ANSI if template rendering fails
            if record.levelno >= logging.ERROR:
                return f"{_DIM}{ts}{_RESET} {_RED}{msg}{_RESET}"
            if record.levelno >= logging.WARNING:
                return f"{_DIM}{ts}{_RESET} {_YELLOW}{msg}{_RESET}"
            return f"{_DIM}{ts}{_RESET} {msg}"


def configure_logging(config: ServerConfig) -> None:
    """Configure stdlib logging for pounce and framework loggers.

    Resolves the ``"auto"`` format: pretty on TTY, JSON when piped.
    Sets up the pounce logger hierarchy and the chirp framework logger
    with the configured log level and appropriate formatter.

    Args:
        config: Server configuration with log_level and log_format settings.

    """
    global _json_logging, _resolved_format
    level = getattr(logging, config.log_level.upper(), logging.INFO)

    # Resolve "auto" format — pretty on an interactive terminal, else JSON.
    # Some IDEs attach stdout as a TTY but not stderr; check both.
    fmt = config.log_format.lower()
    if fmt == "auto":
        interactive = sys.stderr.isatty() or sys.stdout.isatty()
        _resolved_format = "pretty" if interactive else "json"
    else:
        _resolved_format = fmt
    _json_logging = _resolved_format == "json"

    formatter: logging.Formatter
    match _resolved_format:
        case "json":
            formatter = _JSONFormatter()
        case "pretty":
            formatter = _PrettyFormatter()
        case _:
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
    else:
        for h in root.handlers:
            h.setFormatter(formatter)

    # Configure chirp framework logger alongside pounce
    chirp_logger = logging.getLogger("chirp")
    chirp_logger.setLevel(level)
    if not chirp_logger.handlers:
        chirp_handler = logging.StreamHandler(sys.stderr)
        chirp_handler.setLevel(level)
        chirp_handler.setFormatter(formatter)
        chirp_logger.addHandler(chirp_handler)
    else:
        for h in chirp_logger.handlers:
            h.setFormatter(formatter)

    # Configure lifecycle logger (inherits from pounce logger)
    logging.getLogger("pounce.lifecycle")
    # No separate handler needed - inherits from "pounce" logger


def access_log(
    method: str,
    path: str,
    status: int,
    bytes_sent: int,
    duration_ms: float,
    client: str,
    *,
    http_version: str = "1.1",
    request_id: str | None = None,
    worker_id: int | None = None,
) -> None:
    """Log an access log entry for a completed request.

    In JSON mode, writes a flat structured JSON line directly to stderr
    (bypassing the stdlib logging formatter to avoid double-nesting).
    In pretty mode, writes a colored compact line to stderr.
    In text mode, uses the standard combined log format via stdlib logging.

    Args:
        method: HTTP method (e.g., "GET").
        path: Request path (e.g., "/api/users").
        status: HTTP response status code.
        bytes_sent: Number of response body bytes sent.
        duration_ms: Request duration in milliseconds.
        client: Client address string (e.g., "127.0.0.1:5000").
        http_version: Protocol version string (e.g., "1.1", "2").
        request_id: Optional request ID for tracing.
        worker_id: Optional worker ID for multi-worker correlation.

    """
    match _resolved_format:
        case "json":
            entry: dict[str, object] = {
                "ts": datetime.now(tz=UTC).isoformat(),
                "level": "warn" if status >= 500 else "info",
                "method": method,
                "path": path,
                "status": status,
                "bytes": bytes_sent,
                "duration_ms": round(duration_ms, 1),
                "client": client,
            }
            if request_id is not None:
                entry["req_id"] = request_id[:8]
            if worker_id is not None:
                entry["worker"] = worker_id
            line = json_module.dumps(entry, default=str)
            with _stderr_lock:
                sys.stderr.write(line + "\n")

        case "pretty":
            from pounce import _output

            _output.access(method, path, status, bytes_sent, duration_ms, client)

        case _:
            # Text mode — classic combined-log via stdlib logging
            level = logging.WARNING if status >= 500 else logging.INFO
            rid_suffix = f" [{request_id[:12]}]" if request_id else ""
            wid_suffix = f" w{worker_id}" if worker_id is not None else ""
            access_logger.log(
                level,
                '%s - "%s %s HTTP/%s" %d %d %.1fms%s%s',
                client,
                method,
                path,
                http_version,
                status,
                bytes_sent,
                duration_ms,
                rid_suffix,
                wid_suffix,
            )
