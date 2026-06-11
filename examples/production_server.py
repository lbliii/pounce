"""
Production server example with all Phase 6 features enabled.

This example demonstrates a production-ready pounce configuration with:
- Prometheus metrics for monitoring
- Rate limiting for abuse protection
- Request queueing for overload handling
- Hot reload for zero-downtime deployments

Sentry error tracking is supported but disabled by default; set the
``SENTRY_DSN`` environment variable to enable it (see the config block
below). The ``sentry-sdk`` package must be installed.

Run:
    python examples/production_server.py

Then visit:
    http://localhost:8000/          - API endpoint
    http://localhost:8000/metrics   - Prometheus metrics
    http://localhost:8000/health    - Health check

Test rate limiting:
    # Send 10 requests rapidly (should get rate limited)
    for i in {1..10}; do curl http://localhost:8000/; done

Test queue overload:
    # Send many concurrent requests to fill queue
    ab -n 1000 -c 50 http://localhost:8000/slow

"""

import asyncio
import os
import time

from pounce import ServerConfig, run


# Sample ASGI application
async def app(scope, receive, send):
    """Sample production API."""
    if scope["type"] != "http":
        return

    path = scope["path"]

    # Health check endpoint
    if path == "/health":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"status": "healthy", "service": "pounce-demo"}',
            }
        )
        return

    # Slow endpoint (for testing queue)
    if path == "/slow":
        await asyncio.sleep(2)  # Simulate slow operation
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"Completed slow operation",
            }
        )
        return

    # Error endpoint (for testing Sentry)
    if path == "/error":
        raise ValueError("Test error for Sentry")

    # Normal API endpoint
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-powered-by", b"pounce"),
            ],
        }
    )

    response = (
        b'{"message": "Hello from production pounce!", '
        b'"timestamp": ' + str(int(time.time())).encode() + b", "
        b'"features": ["metrics", "rate-limiting", "queueing", "hot-reload"]}'
    )

    await send(
        {
            "type": "http.response.body",
            "body": response,
        }
    )


if __name__ == "__main__":
    # Production configuration with all Phase 6 features
    config = ServerConfig(
        # Basic server config.
        # 0.0.0.0 binds all interfaces (a deliberate production demo). The
        # open /metrics endpoint below MUST be firewalled or placed behind
        # auth before exposing this server publicly.
        host="0.0.0.0",  # intentional public bind for the demo
        port=8000,
        workers=4,  # Multiple workers for zero-downtime reload
        # Built-in health check
        health_check_path="/health",
        # Phase 6.1: Prometheus Metrics
        # SECURITY: /metrics is served with no auth. Before exposing this
        # server publicly, firewall /metrics (or place it behind auth / a
        # reverse proxy) so internal metrics are not leaked to the internet.
        metrics_enabled=True,
        metrics_path="/metrics",
        # Phase 6.2: Rate Limiting & Backpressure
        rate_limit_enabled=True,
        rate_limit_requests_per_second=10.0,  # 10 req/s per IP (low for demo)
        rate_limit_burst=20,  # Allow bursts up to 20
        # Phase 6.3: Request Queueing & Load Shedding
        request_queue_enabled=True,
        request_queue_max_depth=100,  # Queue up to 100 requests
        # Phase 6.4: Sentry Error Tracking (optional, requires sentry-sdk).
        # Enabled only when SENTRY_DSN is set in the environment, so the
        # advertised feature is real when configured and inert otherwise.
        sentry_dsn=os.getenv("SENTRY_DSN"),
        sentry_environment="production",
        sentry_traces_sample_rate=0.1,
        # Phase 6.5: Hot Reload
        reload_timeout=30.0,  # Wait 30s for workers to drain during reload
        # Additional production features
        lifecycle_logging=True,  # Structured event logging
        log_format="json",  # JSON logs for production
        log_level="info",
        # Performance tuning
        max_connections=1000,
        backlog=2048,
        keep_alive_timeout=5.0,
        request_timeout=30.0,
        # Compression
        compression=True,
        compression_min_size=500,
    )

    print("🚀 Starting production pounce server...")
    print()
    print("Features enabled:")
    print("  ✅ Prometheus metrics:  http://localhost:8000/metrics")
    print("  ✅ Rate limiting:       10 req/s per IP")
    print("  ✅ Request queueing:    Max 100 queued")
    print("  ✅ Health check:        http://localhost:8000/health")
    print("  ✅ Hot reload:          Send SIGHUP for zero-downtime reload")
    print()
    print("Test endpoints:")
    print("  GET  /           - Normal API response")
    print("  GET  /slow       - Slow endpoint (2s delay)")
    print("  GET  /error      - Trigger error (for Sentry)")
    print("  GET  /health     - Health check")
    print("  GET  /metrics    - Prometheus metrics")
    print()
    print("Trigger hot reload:")
    print(f"  kill -SIGHUP {os.getpid()}")
    print()

    run(app, config=config)
