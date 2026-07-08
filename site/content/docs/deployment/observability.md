---
title: Observability
description: Health checks, request tracing, Prometheus metrics, OpenTelemetry, and Sentry
draft: false
weight: 50
lang: en
type: doc
tags: [observability, health-check, metrics, tracing, opentelemetry, sentry, introspection]
keywords: [health-check, prometheus, metrics, request-id, tracing, opentelemetry, sentry, monitoring, introspection]
category: how-to
---

# Observability

Pounce provides six observability layers: health checks, request IDs, Prometheus metrics, OpenTelemetry tracing, Sentry error tracking, and the opt-in `/_pounce/info` introspection endpoint.

## Readiness and Liveness

Configure Pounce's built-in endpoint as `/readyz`. It bypasses the ASGI app and
reports whether this worker should receive new traffic:

```bash
pounce serve --app myapp:app --health-check-path /readyz
```

Response:

```json
{"status": "ok", "uptime_seconds": 3600.1, "worker_id": 0, "active_connections": 42}
```

Characteristics: fast (bypasses ASGI), excluded from access logs, works even if your app is unhealthy, includes `Cache-Control: no-cache`.

During drain it returns HTTP 503 with `{"status":"draining"}` before the
worker exits. `HEAD /readyz` returns the same status and headers as `GET`
without a body. Once final connection rejection starts, a probe may instead
receive the generic drain 503 or a refused connection; those are also
not-ready outcomes.

Use `/healthz` for liveness only when your platform needs a separate process
health signal. That endpoint is application-owned: keep it successful while
the process can make forward progress, and do not use it for load-balancer
de-registration.

### Kubernetes

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /readyz
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

The request id is logged **in full** in both `json` and `text` access-log
modes, so the `req_id` access-log field is byte-for-byte equal to the
`X-Request-ID` response header. Correlate a log line with a client-side
response (or a trusted proxy's forwarded id) by exact string match — no
prefix logic is required. Because a trusted proxy may supply a non-UUID4
value of any length, no truncation or length assumption is applied.

## JSON access-log schema

`--log-format json` emits one flat JSON object per completed request on
stderr. **This schema is a stability contract**: log-ingestion pipelines may
depend on the field names, value types, and the `req_id` policy below.

```json
{"ts": "2026-02-08T12:00:00+00:00", "level": "warn", "method": "GET", "path": "/", "status": 500, "bytes": 21, "duration_ms": 98.9, "client": "127.0.0.1:5000", "req_id": "a1b2c3d4e5f67890a1b2c3d4e5f67890", "worker": 0}
```

| Key | Type | Always present | Meaning |
|-----|------|----------------|---------|
| `ts` | string | yes | ISO-8601 timestamp, UTC, with offset |
| `level` | string | yes | `"info"` for status < 500, `"warn"` for status >= 500 |
| `method` | string | yes | HTTP request method |
| `path` | string | yes | Request target (path + query string) |
| `status` | integer | yes | HTTP response status code |
| `bytes` | integer | yes | Response body bytes sent |
| `duration_ms` | number | yes | Request duration in ms, rounded to 1 decimal |
| `client` | string | yes | Peer address as `host:port` |
| `req_id` | string | no | Full request id; equals the `X-Request-ID` header. Present only when a request id exists |
| `worker` | integer | no | Worker id; present only in multi-worker mode |

Stability guarantees:

- Existing keys are not renamed or retyped without a deprecation cycle.
- New optional keys may be added over time, so consumers should ignore
  unknown keys rather than reject the line.
- `req_id`, when present, is the **complete** id (never truncated), matching
  the `X-Request-ID` response header exactly.

The `text` access-log format is intended for humans and is **not** covered by
this contract; use `json` for machine consumption.

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

Pounce exports request traces over OTLP/HTTP. It does not export OpenTelemetry
metrics; use the Prometheus endpoint above for server metrics.

The request-span contract is:

| Signal | Guaranteed value |
|---|---|
| Span name | `{METHOD}`; never the raw request path |
| Span kind | `SERVER` |
| Resource | `service.name` from `otel_service_name` |
| Request attributes | `http.request.method`, `url.path`, `url.scheme`, `server.address`, `server.port` |
| Response attributes | `http.response.status_code`; `http.response.body.size` when the recorded body size is positive |
| Status | `OK` for HTTP 1xx–4xx; `ERROR` for 5xx responses and recorded exceptions |
| Exceptions | Standard `exception` span event plus `ERROR` status |

Incoming W3C `traceparent` and `tracestate` headers are parsed so the server
span continues the upstream trace. Pounce does not emit the deprecated
`http.method`, `http.target`, or `http.status_code` attribute names.

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

## Introspection Endpoint

`/_pounce/info` is an opt-in JSON endpoint that exposes pounce's live runtime
state for debugging a running server. Like the health check, it is dispatched
before the request reaches your ASGI app, so it works even when your app is
unhealthy.

### Enable it

Introspection is **disabled by default**. Three `ServerConfig` fields control it:

| Option | Default | Description |
|---|---|---|
| `introspection_enabled` | `False` | Master switch. No endpoint is registered while `False`. |
| `introspection_bind` | `"127.0.0.1"` | Public-exposure **warning policy** input, not a separate listener. A non-loopback value triggers the startup warning below. |
| `introspection_path` | `"/_pounce/info"` | Path the endpoint is served on. Built-in dispatch wins over a colliding user route while introspection is enabled. |

```python
from pounce import ServerConfig

config = ServerConfig(introspection_enabled=True)  # loopback-only, /_pounce/info
```

```toml
# pounce.toml
[tool.pounce]
introspection_enabled = true
```

### Query it

The endpoint shares the main application listener, so on a default
(loopback) bind you reach it from the same host:

```bash
curl http://127.0.0.1:8000/_pounce/info
```

```json
{
  "runtime": {
    "python_version": "3.14.0",
    "gil_enabled": false,
    "worker_mode": "sync",
    "uptime_seconds": 3600.1
  },
  "worker": {
    "worker_id": 0,
    "active_connections": 42
  },
  "config": {
    "compression": true,
    "host_set": true,
    "port": 8000,
    "ssl_certfile_set": false,
    "workers": 4
  }
}
```

The response carries `Content-Type: application/json` and
`Cache-Control: no-cache, no-store`.

### Redaction

The `config` section is **not** a raw config dump. It is filtered through the
`INFO_ALLOWLIST` allowlist (`src/pounce/_config_schema.py`), which is
fail-closed:

- Non-sensitive fields are **exposed** with their values (e.g. `port`, `workers`, `compression`).
- Sensitive fields are **redacted to a boolean** — a field like `ssl_certfile` surfaces only as `ssl_certfile_set: true/false`, never its value. The same applies to `sentry_dsn`, `host`, `trusted_hosts`, `root_path`, `uds`, and other secret-bearing fields.
- Any field not listed in the allowlist is **omitted entirely**.

So raw secrets never appear in the body. The runtime fingerprint (version,
GIL state, worker count, uptime) is still informative to anyone who can reach
the endpoint.

### Public-bind warning

There is no token auth — if you need authentication, put the endpoint behind
your reverse proxy. To make accidental exposure hard to miss, pounce emits a
startup `WARNING` when introspection is enabled while the main `host` (or
`introspection_bind`) is a **non-loopback** address:

```text
POUNCE_CONFIG_INTROSPECTION_PUBLIC: introspection endpoint enabled with a
non-loopback bind. The endpoint exposes runtime state; keep it loopback-only,
disable introspection, or block the path at your reverse proxy.
```

Loopback literals (`127.0.0.1`, `::1`, `localhost`) do not trigger the warning.
In production, keep introspection loopback-only, set
`introspection_enabled=False`, or have your proxy strip `introspection_path`
from external traffic. See the
[[docs/configuration/server-config|ServerConfig]] reference and the
`POUNCE_CONFIG_INTROSPECTION_PUBLIC` troubleshooting entry for details.

## Lifecycle Events

All observability features build on pounce's structured lifecycle event system. Every connection emits immutable events: `ConnectionOpened`, `RequestStarted`, `ResponseCompleted`, `RequestFailed`, `ClientDisconnected`, `ConnectionClosed`. These flow to any `LifecycleCollector` implementation.

## See Also

- [[docs/deployment/production|Production]] -- Full deployment guide
- [[docs/features/lifecycle-logging|Lifecycle Logging]] -- Structured event logging
- [[docs/configuration/server-config|ServerConfig]] -- All configuration options
