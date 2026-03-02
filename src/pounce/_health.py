"""
Built-in health check endpoint.

Responds to GET requests at the configured ``health_check_path`` before
the request reaches the ASGI application.  Returns a JSON payload with
server status, uptime, active connections, and worker identity.

Skips access logging by default (health checks are noisy in production).

"""

import json
import time

_SERVER_START_NS: int = time.monotonic_ns()


def build_health_response(
    *,
    worker_id: int,
    active_connections: int,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Build a health check response.

    Returns:
        Tuple of (status_code, headers, body).

    """
    uptime_s = (time.monotonic_ns() - _SERVER_START_NS) / 1_000_000_000
    payload = json.dumps(
        {
            "status": "ok",
            "uptime_seconds": round(uptime_s, 1),
            "worker_id": worker_id,
            "active_connections": active_connections,
        }
    ).encode("utf-8")

    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
        (b"cache-control", b"no-cache, no-store"),
    ]

    return 200, headers, payload
