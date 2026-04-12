---
title: Observability
description: Health checks, request tracing, Prometheus metrics, OpenTelemetry, and Sentry
draft: false
weight: 50
lang: en
type: doc
tags: [observability, health-check, metrics, tracing, opentelemetry, sentry]
keywords: [health-check, prometheus, metrics, request-id, tracing, opentelemetry, sentry, monitoring]
category: how-to
---

# Observability

Pounce provides five observability layers: health checks, request IDs, Prometheus metrics, OpenTelemetry tracing, and Sentry error tracking.

## Health Checks

Built-in endpoint that responds before the ASGI app is invoked:

```bash
pounce serve myapp:app --health-check-path /health
```

Response:

```json
{"status": "ok", "uptime_seconds": 3600.1, "worker_id": 0, "active_connections": 42}
```

Characteristics: fast (bypasses ASGI), excluded from access logs, works even if your app is unhealthy, includes `Cache-Control: no-cache`.

### Kubernetes

```yaml
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

## Request IDs

Every request gets a unique identifier for end-to-end tracing:

1. If a trusted proxy sends `X-Request-ID`, pounce uses that value
2. Otherwise, pounce generates a UUID4 hex string (32 chars, no dashes)
3. The ID is injected into response headers, `scope["extensions"]["request_id"]`, and access logs

Access your app's request ID:

```python
async def app(scope, receive, send):
    request_id = scope.get("extensions", {}).get("request_id")
```

## Prometheus Metrics

`PrometheusCollector` implements the `LifecycleCollector` protocol. Thread-safe for free-threading mode.

### Setup

```python
from pounce import ServerConfig
from pounce.metrics import PrometheusCollector
from pounce.server import Server

collector = PrometheusCollector()
config = ServerConfig(host="0.0.0.0", workers=4)
server = Server(config, app, lifecycle_collector=collector)
```

Or use the built-in metrics endpoint:

```python
config = ServerConfig(
    metrics_enabled=True,
    metrics_path="/metrics",  # default
)
```

### Metrics

| Metric | Type | Description |
|---|---|---|
| `http_requests_total` | Counter | Requests by status code |
| `http_request_duration_seconds` | Histogram | Request duration distribution |
| `http_connections_active` | Gauge | Open TCP connections |
| `http_requests_in_flight` | Gauge | Requests being processed |
| `http_bytes_sent_total` | Counter | Total response bytes |

### Programmatic Access

```python
data = collector.snapshot()
# {"requests_total": {("", "200"): 1523}, "connections_active": 42, ...}

text = collector.export()  # Prometheus text exposition format
```

## OpenTelemetry

Native distributed tracing with automatic span creation and W3C Trace Context propagation.

### Setup

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

```python
config = ServerConfig(
    otel_endpoint="http://localhost:4318",
    otel_service_name="my-api",
)
```

OTel is disabled by default. Setting `otel_endpoint` enables it.

### What Gets Traced

Every request creates a span named `{METHOD} {path}` with HTTP semantic convention attributes (`http.method`, `http.target`, `http.status_code`, etc.). Incoming `traceparent` headers are parsed to continue distributed traces. Unhandled exceptions are recorded on spans with `ERROR` status.

### Platform Examples

| Platform | Endpoint |
|---|---|
| Jaeger | `http://localhost:4318` |
| Datadog Agent | `http://localhost:4318` |
| Grafana Tempo | `http://tempo:4318` |
| Honeycomb | `https://api.honeycomb.io` |

Pounce appends `/v1/traces` automatically.

### Sampling

Spans are batched (default: every 5s or 512 spans). For high-traffic apps, configure OTel SDK sampling:

```python
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
sampler = TraceIdRatioBased(0.1)  # 10%
```

### Troubleshooting

- **"package not installed"**: `pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http`
- **Traces not appearing**: Verify collector is running (`curl http://localhost:4318/v1/traces`), check pounce logs
- **Context not propagating**: Install `opentelemetry-instrumentation-httpx` for automatic HTTP client instrumentation

## Sentry

Automatic error tracking and performance monitoring.

### Setup

```bash
pip install sentry-sdk
```

```python
config = ServerConfig(
    sentry_dsn="https://key@o0.ingest.sentry.io/0",
    sentry_environment="production",
    sentry_release="myapp@1.0.0",
    sentry_traces_sample_rate=0.1,   # 10% of requests
    sentry_profiles_sample_rate=0.1,
)
```

| Option | Default | Description |
|---|---|---|
| `sentry_dsn` | `None` | Sentry DSN (None = disabled) |
| `sentry_environment` | `None` | Environment name |
| `sentry_release` | `None` | Release version |
| `sentry_traces_sample_rate` | `0.1` | Performance sample rate (0.0-1.0) |
| `sentry_profiles_sample_rate` | `0.1` | Profiling sample rate (0.0-1.0) |

### What Gets Captured

- **Exceptions**: Automatically captured from ASGI apps with full request context (method, path, sanitized headers, client IP)
- **Performance**: Request duration, database queries, external API calls (at configured sample rate)
- **Breadcrumbs**: Debug context for error reports

### Sampling Strategy

| Environment | Traces | Profiles |
|---|---|---|
| Production (high traffic) | 0.01 (1%) | 0.01 |
| Staging | 0.5 (50%) | 0.1 |
| Development | 1.0 (100%) | 0.0 |

### Troubleshooting

- **No events**: Verify DSN, ensure `sentry-sdk` is installed, check pounce logs for init messages
- **High overhead**: Lower sample rates, disable profiling, filter noisy events with `before_send`

## Lifecycle Events

All observability features build on pounce's structured lifecycle event system. Every connection emits immutable events: `ConnectionOpened`, `RequestStarted`, `ResponseCompleted`, `RequestFailed`, `ClientDisconnected`, `ConnectionClosed`. These flow to any `LifecycleCollector` implementation.

## See Also

- [[docs/deployment/production|Production]] -- Full deployment guide
- [[docs/features/lifecycle-logging|Lifecycle Logging]] -- Structured event logging
- [[docs/configuration/server-config|ServerConfig]] -- All configuration options
