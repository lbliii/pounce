---
title: Observability
description: Health checks, request tracing, and Prometheus metrics
draft: false
weight: 50
lang: en
type: doc
tags: [observability, health-check, metrics, tracing, request-id]
keywords: [health-check, prometheus, metrics, request-id, tracing, monitoring, kubernetes]
category: how-to
---

## Overview

Pounce provides three observability primitives out of the box — health checks, request IDs, and Prometheus-compatible metrics — with zero external dependencies.

## Health Checks

Enable a built-in health endpoint that responds before the ASGI app is invoked:

```bash
pounce myapp:app --health-check-path /health
```

### Response

```json
{
    "status": "ok",
    "uptime_seconds": 3600.1,
    "worker_id": 0,
    "active_connections": 42
}
```

The response includes `Cache-Control: no-cache, no-store` to prevent caching by intermediaries.

### Characteristics

- **Fast** — Responds at the worker level, before ASGI dispatch
- **Silent** — Excluded from access logs to reduce noise
- **Independent** — Works even if your ASGI app is unhealthy
- **Lightweight** — JSON payload with status, uptime, worker ID, and connection count

### Kubernetes Integration

```yaml
apiVersion: v1
kind: Pod
spec:
    containers:
        - name: app
          livenessProbe:
              httpGet:
                  path: /health
                  port: 8000
              initialDelaySeconds: 5
              periodSeconds: 10
          readinessProbe:
              httpGet:
                  path: /health
                  port: 8000
              initialDelaySeconds: 2
              periodSeconds: 5
```

### Load Balancer Integration

Point your load balancer's health check at the configured path. Since health checks bypass the ASGI app, they reflect the server's true availability — not application-level readiness.

## Request IDs

Every request is assigned a unique identifier for end-to-end tracing.

### How It Works

1. If a **trusted proxy** sends `X-Request-ID`, Pounce uses that value
2. Otherwise, Pounce generates a new UUID4 hex string (32 characters, no dashes)
3. The ID is injected into:
   - **Response headers** — `X-Request-ID` on every response
   - **ASGI scope** — `scope["extensions"]["request_id"]`
   - **Access logs** — Both text and JSON formats

### Access Log Format

**Text mode:**

```
127.0.0.1:5000 - "GET / HTTP/1.1" 200 1234 5.2ms [a1b2c3d4e5f6]
```

The request ID is truncated to 12 characters in text mode for readability.

**JSON mode:**

```json
{
    "timestamp": "2026-02-09T12:00:00+00:00",
    "level": "INFO",
    "logger": "pounce.access",
    "method": "GET",
    "path": "/",
    "http_version": "1.1",
    "status": 200,
    "bytes_sent": 1234,
    "duration_ms": 5.2,
    "client": "127.0.0.1:5000",
    "request_id": "a1b2c3d4e5f67890abcdef1234567890"
}
```

### App-Level Access

Your ASGI app can access the request ID from the scope:

```python
async def app(scope, receive, send):
    request_id = scope.get("extensions", {}).get("request_id")
    # Use in your own logging, pass to downstream services, etc.
```

### Nginx Forwarding

To propagate request IDs from nginx, add `X-Request-ID` as a proxy header and configure `trusted_hosts`:

```nginx
proxy_set_header X-Request-ID $request_id;
```

## Prometheus Metrics

Pounce includes a `PrometheusCollector` that implements the `LifecycleCollector` protocol. It tracks standard HTTP server metrics from lifecycle events with zero external dependencies.

### Setup

```python
from pounce import ServerConfig
from pounce.metrics import PrometheusCollector
from pounce.server import Server

collector = PrometheusCollector()
config = ServerConfig(host="0.0.0.0", workers=4)
server = Server(config, app, lifecycle_collector=collector)
```

### Metrics

| Metric | Type | Description |
|---|---|---|
| `http_requests_total` | Counter | Total requests by status code |
| `http_request_duration_seconds` | Histogram | Request duration distribution |
| `http_connections_active` | Gauge | Currently open TCP connections |
| `http_requests_in_flight` | Gauge | Requests currently being processed |
| `http_bytes_sent_total` | Counter | Total response bytes sent |

### Export Format

Call `collector.export()` to get Prometheus text exposition format:

```
# HELP http_requests_total Total HTTP requests.
# TYPE http_requests_total counter
http_requests_total{method="unknown",status="200"} 1523
http_requests_total{method="unknown",status="404"} 12

# HELP http_request_duration_seconds Request duration in seconds.
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{le="0.005"} 800
http_request_duration_seconds_bucket{le="0.01"} 1200
http_request_duration_seconds_bucket{le="0.025"} 1400
...
http_request_duration_seconds_bucket{le="+Inf"} 1535
http_request_duration_seconds_sum 45.678
http_request_duration_seconds_count 1535

# HELP http_connections_active Active TCP connections.
# TYPE http_connections_active gauge
http_connections_active 42

# HELP http_requests_in_flight Requests currently being processed.
# TYPE http_requests_in_flight gauge
http_requests_in_flight 3

# HELP http_bytes_sent_total Total bytes sent in responses.
# TYPE http_bytes_sent_total counter
http_bytes_sent_total 15234567
```

### Serving Metrics

Expose a `/metrics` endpoint in your ASGI app:

```python
from pounce.metrics import PrometheusCollector

collector = PrometheusCollector()

async def app(scope, receive, send):
    if scope["path"] == "/metrics":
        body = collector.export().encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain; version=0.0.4; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
        return
    # ... rest of your app
```

### Thread Safety

`PrometheusCollector` uses `threading.Lock` internally — safe for concurrent access from multiple workers in free-threading mode.

### JSON Snapshot

For programmatic access, use `collector.snapshot()`:

```python
data = collector.snapshot()
# {
#     "requests_total": {("", "200"): 1523, ("", "404"): 12},
#     "duration_sum_seconds": 45.678,
#     "duration_count": 1535,
#     "connections_active": 42,
#     "requests_in_flight": 3,
#     "bytes_sent_total": 15234567,
# }
```

## Lifecycle Events

All observability features build on Pounce's structured lifecycle event system. Every connection and request emits immutable events:

| Event | When |
|---|---|
| `ConnectionOpened` | TCP connection accepted |
| `RequestStarted` | HTTP request headers parsed |
| `ResponseCompleted` | Response fully sent |
| `RequestFailed` | Request handler raised an exception |
| `ClientDisconnected` | Client disconnected mid-request |
| `ConnectionClosed` | TCP connection closed |

These events flow to any `LifecycleCollector` — the `PrometheusCollector` is one implementation, but you can write your own for custom metrics, tracing, or event sourcing.

## See Also

- [[docs/deployment/production|Production]] — Full production deployment guide
- [[docs/deployment/security|Security]] — Security hardening features
- [[docs/configuration/server-config|ServerConfig]] — All configuration options
