"""
Built-in ``/_pounce/info`` introspection endpoint (Sprint 5).

Disabled by default. When ``ServerConfig.introspection_enabled`` is True,
the worker dispatches GET requests at ``introspection_path`` to
:func:`build_introspect_response` before the request reaches the ASGI
application.

The response body is a JSON object with three sections:

- **runtime**: Pounce and operator build identity, Python build and GIL state,
  configured worker mode, resolved worker model, and server uptime.
- **worker**: per-worker identity (``worker_id``) and live counters
  (``active_connections``).
- **config**: the :func:`~pounce._config_schema.redacted_config_view` of
  the active :class:`~pounce.config.ServerConfig` — fail-closed via
  ``INFO_ALLOWLIST``.

See ``docs/troubleshooting.md#POUNCE_CONFIG_INTROSPECTION_PUBLIC`` for the
warning emitted when the endpoint is bound non-loopback.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from typing import TYPE_CHECKING

from pounce import __version__
from pounce._config_schema import redacted_config_view
from pounce._runtime import (
    is_free_threaded_build,
    is_gil_enabled,
    resolve_worker_model,
)

if TYPE_CHECKING:
    from pounce.config import ServerConfig

_SERVER_START_NS: int = time.monotonic_ns()


def _python_build_identity() -> dict[str, str | bool]:
    """Return non-sensitive fields that identify the running Python build."""
    build_number, build_date = platform.python_build()
    return {
        "implementation": platform.python_implementation(),
        "build_number": build_number,
        "build_date": build_date,
        "compiler": platform.python_compiler(),
        "free_threaded": is_free_threaded_build(),
    }


def _operator_build_id() -> str | None:
    """Return the explicitly public operator identity, or ``None`` when unset."""
    return os.environ.get("POUNCE_BUILD_ID") or None


def build_introspect_response(
    *,
    config: ServerConfig,
    worker_id: int,
    active_connections: int,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Build the ``/_pounce/info`` JSON response.

    Returns:
        Tuple of (status_code, headers, body) — same shape as
        :func:`pounce._health.build_health_response`.
    """
    uptime_s = (time.monotonic_ns() - _SERVER_START_NS) / 1_000_000_000
    payload = {
        "runtime": {
            "pounce_version": __version__,
            "build_id": _operator_build_id(),
            "python_version": sys.version.split()[0],
            "python_build": _python_build_identity(),
            "gil_enabled": is_gil_enabled(),
            "worker_mode": config.worker_mode,
            "worker_model": resolve_worker_model(config.worker_mode, config.resolve_workers()),
            "uptime_seconds": round(uptime_s, 1),
        },
        "worker": {
            "worker_id": worker_id,
            "active_connections": active_connections,
        },
        "config": redacted_config_view(config),
    }
    body = json.dumps(payload).encode("utf-8")

    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-cache, no-store"),
    ]

    return 200, headers, body
