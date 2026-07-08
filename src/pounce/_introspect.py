"""
Built-in ``/_pounce/info`` introspection endpoint (Sprint 5).

Disabled by default. When ``ServerConfig.introspection_enabled`` is True,
the worker dispatches GET requests at ``introspection_path`` to
:func:`build_introspect_response` before the request reaches the ASGI
application.

The response body is a JSON object with three sections:

- **runtime**: Python version, GIL state, configured worker mode, resolved
  worker model, and server uptime.
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
import sys
import time
from typing import TYPE_CHECKING

from pounce._config_schema import redacted_config_view
from pounce._runtime import resolve_worker_model

if TYPE_CHECKING:
    from pounce.config import ServerConfig

_SERVER_START_NS: int = time.monotonic_ns()


def _gil_enabled() -> bool:
    """Return whether the GIL is enabled in this interpreter.

    Returns True on standard CPython, False on free-threaded 3.14t+.
    Falls back to True if the API isn't available (pre-3.13 builds).
    """
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    if is_enabled is None:
        return True
    return bool(is_enabled())


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
            "python_version": sys.version.split()[0],
            "gil_enabled": _gil_enabled(),
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
