---
title: ServerConfig
description: The frozen dataclass that controls all server behavior
draft: false
weight: 10
lang: en
type: doc
tags: [configuration, serverconfig, dataclass]
keywords: [serverconfig, configuration, frozen, dataclass, settings]
category: reference
---

## Overview

`ServerConfig` is a frozen dataclass (`@dataclass(frozen=True, slots=True)`)
that holds all server settings. It's created once at startup and shared across
workers as frozen server configuration. Treat referenced objects as read-only
unless their own API says otherwise.

```python
from pounce import ServerConfig

config = ServerConfig(
    host="0.0.0.0",
    port=8000,
    workers=4,
    compression=True,
    server_timing=True,
    health_check_path="/readyz",
)
```

## Stability Tiers

Not every knob carries the same maturity. Use the tier to decide what to lean on
in production. The same classification is surfaced programmatically:
`pounce config schema` stamps an `x-stability` annotation on each beta field.

| Tier | Meaning |
|------|---------|
| **Stable** | Covered by the [core contract](https://github.com/lbliii/pounce/blob/main/docs/design/core-contract.md). Safe for production; behavior changes follow deprecation policy. |
| **Beta** | Usable, but the behavior, surface, or proof is still firming up. Pin versions and validate in staging before relying on it. |

**Stable knobs:** `host`/`port`/`uds`, `workers`/`backlog`, `worker_mode`
values `auto`/`sync`/`async`, the timeouts (`*_timeout`), the limits (`max_*`),
and logging (`access_log`/`log_level`/`log_format`).

**Beta knobs:** `worker_mode="subinterpreter"` (PEP 734 — thread-like speed with
process-like isolation, but limited lifecycle proof), rate limiting
(`rate_limit_*`), request queueing (`request_queue_*`), introspection
(`introspection_*`), HTTP/3 (`http3_*`), and the observability integrations
(`otel_*`, `sentry_*`, `metrics_*`).

::::{note}
Beta is about *maturity*, not safety: beta knobs are off by default and removable
from the request path when disabled. Inspect the live classification with
`pounce config schema` (look for `"x-stability": "beta"`).
::::

## Core Fields

### Bind Address

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | `str` | `"127.0.0.1"` | Bind address |
| `port` | `int` | `8000` | Bind port (0-65535) |
| `uds` | `str \| None` | `None` | Unix domain socket path. Mutually exclusive with `host`/`port`. |
| `uds_permissions` | `int` | `0o660` | File mode applied to created Unix domain sockets. |

### Workers

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `workers` | `int` | `1` | Worker count. 0 = auto-detect from CPU cores. 1 = single-worker (no supervisor). 2+ = multi-worker with supervisor. |
| `worker_mode` | `str` | `"auto"` | Worker execution model: `auto` (sync on 3.14t, async on GIL), `sync` (blocking I/O fast path), `async` (event loop), or `subinterpreter` (**beta**, PEP 734). |
| `backlog` | `int` | `2048` | Socket listen backlog |
| `cpu_affinity` | `bool` | `False` | Pin each worker to a CPU core (Linux only, reduces cache thrashing) |
| `executor_threads_per_worker` | `int` | `0` | Per-worker thread pool size for `asyncio.to_thread()`. 0 = auto-size. |

### Timeouts

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `keep_alive_timeout` | `float` | `5.0` | Seconds to keep idle connections open |
| `header_timeout` | `float` | `10.0` | Seconds to receive complete request headers (slowloris protection) |
| `request_timeout` | `float` | `30.0` | Maximum seconds for a complete request |
| `startup_timeout` | `float` | `30.0` | Maximum seconds to wait for server startup |
| `shutdown_timeout` | `float` | `10.0` | Seconds per worker join during shutdown (parallel in multi-worker) |

### Limits

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_request_size` | `int` | `1,048,576` | Maximum request body (1 MB) |
| `max_header_size` | `int` | `65,536` | Maximum total header size (64 KB) |
| `max_headers` | `int` | `100` | Maximum number of headers |
| `max_connections` | `int` | `10,000` | Maximum concurrent connections |
| `max_requests_per_connection` | `int` | `0` | Max requests per keep-alive connection (0 = unlimited) |

### Logging

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `access_log` | `bool` | `True` | Enable access logging |
| `log_level` | `str` | `"info"` | Log level: debug, info, warning, error, critical |
| `log_format` | `str` | `"auto"` | Log output format: `auto` (pretty on TTY, JSON when piped), `text`, or `json` |
| `display` | `DisplayConfig \| None` | `None` | Optional startup display configuration. |
| `app_name` | `str \| None` | `None` | App name merged into startup display configuration. |
| `app_tagline` | `str \| None` | `None` | App tagline merged into startup display configuration. |
| `app_version` | `str \| None` | `None` | App version merged into startup display configuration. |
| `signage` | `str \| None` | `None` | Startup signage style override. |
| `access_log_filter` | `Callable[[str, str, int], bool] \| None` | `None` | Optional filter: `(method, path, status) -> bool`. True = log, False = skip. |

### HTTP

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_header` | `str` | `"pounce"` | Value of the `Server` response header |
| `date_header` | `bool` | `True` | Include `Date` response header |
| `root_path` | `str` | `""` | ASGI root_path for reverse proxy setups |

### Compression

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `compression` | `bool` | `True` | Enable content-encoding negotiation |
| `compression_min_size` | `int` | `500` | Minimum response size in bytes to compress |

### Observability

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_timing` | `bool` | `False` | Inject `Server-Timing` header with parse/app/encode durations |
| `health_check_path` | `str \| None` | `None` | Path for the built-in readiness endpoint (recommended: `"/readyz"`). Returns 503 while draining; disabled by default. |
| `introspection_enabled` | `bool` | `False` | Enable the built-in `/_pounce/info` endpoint. |
| `introspection_bind` | `str` | `"127.0.0.1"` | Warning-policy bind value for introspection exposure. |
| `introspection_path` | `str` | `"/_pounce/info"` | Path for the built-in introspection endpoint. |

::::{note}
Request IDs are always generated (or extracted from trusted proxies). Every response includes an `X-Request-ID` header for tracing, and requests from trusted proxies that send `X-Request-ID` have their IDs honoured.
::::

### Development

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `debug` | `bool` | `False` | Enable rich error pages (never use in production) |
| `reload` | `bool` | `False` | Watch source files and restart workers on changes |
| `reload_include` | `tuple[str, ...]` | `()` | Extra file extensions to watch beyond the default set (e.g. `(".rst", ".scss")`) |
| `reload_dirs` | `tuple[str, ...]` | `()` | Extra directories to watch alongside the current working directory |

When `reload` is enabled, the default watch set is `.py`, `.pyi`, `.yaml`,
`.yml`, `.toml`, `.json`, `.cfg`, `.ini`, `.md`, `.html`, `.css`, `.js`, `.svg`.
Static-site authoring edits (Markdown, HTML, CSS, JS, SVG) therefore trigger a
reload without `reload_include`. The watcher scans the current working
directory, every `reload_dirs` entry, and the directories behind any
configured `static_files` mounts; assets stored elsewhere need a matching
`reload_dirs` entry.

### Protocol Tuning

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `h11_max_incomplete_event_size` | `int \| None` | `None` | h11 parser buffer limit (None = h11 default 16 KB) |

### Security

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `trusted_hosts` | `frozenset[str]` | `frozenset()` | Trusted proxy hosts for X-Forwarded-* header validation (empty = strip all proxy headers). Accepts any iterable; normalized to frozenset internally. |

::::{note}
When `trusted_hosts` is empty, Pounce strips `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host` from all requests. Set it to your reverse proxy's IP (e.g. `frozenset({"10.0.0.1"})`) or `frozenset({"*"})` to trust all peers. Trusted `X-Forwarded-Host` rewrites both `scope["server"]` and the ASGI `Host` header, including a forwarded port when present. Tuples and lists are also accepted and converted automatically.
::::

### TLS

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ssl_certfile` | `str \| None` | `None` | Path to TLS certificate file |
| `ssl_keyfile` | `str \| None` | `None` | Path to TLS private key file |

::::{note}
`ssl_certfile` and `ssl_keyfile` must both be set or both be `None`. Setting only one raises `ValueError`.
::::

## Extended Fields

These fields control optional features. Most have sensible defaults and don't need to be set for basic usage.

::::{dropdown} Static Files
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `static_files` | `dict[str, str]` | `{}` | URL path to directory mapping (e.g. `{"/static": "./public"}`) |
| `static_cache_control` | `str` | `"public, max-age=3600"` | Cache-Control header for static files |
| `static_precompressed` | `bool` | `True` | Serve `.zst`/`.gz` pre-compressed variants when available |
| `static_follow_symlinks` | `bool` | `False` | Allow following symlinks (keep disabled in production) |
| `static_index_file` | `str \| None` | `"index.html"` | Index file for directory requests |
::::

::::{dropdown} Middleware
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `middleware` | `list[Callable[..., Any]]` | `[]` | Middleware hooks. Dispatched by parameter count: 1 param = pre-request, 2 params = exception handler, 3 params = post-response. |
::::

::::{dropdown} WebSocket
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `websocket_compression` | `bool` | `True` | Enable permessage-deflate compression |
| `websocket_max_message_size` | `int` | `10,485,760` | Maximum WebSocket message size (10 MB) |
::::

::::{dropdown} HTTP/3 (QUIC)
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `http3_enabled` | `bool` | `False` | Enable HTTP/3 (requires `ssl_certfile` and `ssl_keyfile`) |
| `http3_max_connections` | `int` | `10,000` | Max concurrent QUIC connections |
| `http3_idle_timeout` | `float` | `30.0` | QUIC idle timeout (seconds) |
| `http3_qpack_max_table_capacity` | `int` | `0` | QPACK dynamic table size. 0 uses static-table-only compression. |
| `http3_zero_rtt_enabled` | `bool` | `False` | Accept TLS 0-RTT at the transport layer; unsafe methods receive 425. |
::::

::::{dropdown} Reload
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `reload_timeout` | `float` | `30.0` | Time to wait for workers to drain during reload |
::::

::::{dropdown} OpenTelemetry
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `otel_endpoint` | `str \| None` | `None` | OTLP endpoint (e.g. `"http://localhost:4318"`) |
| `otel_service_name` | `str` | `"pounce"` | Service name in traces |
::::

::::{dropdown} Lifecycle Logging
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `lifecycle_logging` | `bool` | `False` | Enable structured lifecycle event logging |
| `log_slow_requests_threshold` | `float` | `5.0` | Log requests slower than this (seconds) |
::::

::::{dropdown} Prometheus Metrics
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `metrics_enabled` | `bool` | `False` | Enable Prometheus metrics endpoint |
| `metrics_path` | `str` | `"/metrics"` | Path for metrics endpoint |
::::

::::{dropdown} Rate Limiting
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rate_limit_enabled` | `bool` | `False` | Enable per-IP rate limiting (enforced per worker) |
| `rate_limit_requests_per_second` | `float` | `100.0` | Requests per second per IP, **per worker** |
| `rate_limit_burst` | `int` | `200` | Maximum burst size per IP, **per worker** |

These limits are enforced **per worker** (shared only in thread mode); with `workers > 1` the aggregate per-IP ceiling is `rate x workers`. See [[docs/deployment/backpressure|Backpressure]].
::::

::::{dropdown} Request Queueing
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `request_queue_enabled` | `bool` | `False` | Enable request queueing and load shedding (per worker) |
| `request_queue_max_depth` | `int` | `1000` | Maximum queued requests **per worker** (0 = unlimited) |

The queue is **per worker in every mode** (it uses a per-event-loop `asyncio.Semaphore`), so the effective shed depth is `max_depth x workers`. See [[docs/deployment/backpressure|Backpressure]].
::::

::::{dropdown} Sentry
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sentry_dsn` | `str \| None` | `None` | Sentry DSN for error tracking (None = disabled) |
| `sentry_environment` | `str \| None` | `None` | Environment name (e.g. `"production"`) |
| `sentry_release` | `str \| None` | `None` | Release version (e.g. `"myapp@1.0.0"`) |
| `sentry_traces_sample_rate` | `float` | `0.1` | Performance monitoring sample rate (0.0-1.0) |
| `sentry_profiles_sample_rate` | `float` | `0.1` | Profiling sample rate (0.0-1.0) |
::::

## Programmatic Usage

```python
import pounce
from pounce import ServerConfig

# Option 1: Pass kwargs to run()
pounce.run("myapp:app", host="0.0.0.0", workers=4)

# Option 2: Create config explicitly
config = ServerConfig(host="0.0.0.0", workers=4)
```

## Auto-Detect Workers

When `workers=0`, Pounce calls `os.cpu_count()` to determine the worker count:

```python
config = ServerConfig(workers=0)
print(config.resolve_workers())  # e.g., 8 on an 8-core machine
```

## See Also

- [[docs/configuration/cli|CLI Reference]] — Command-line equivalents
- [[docs/configuration/tls|TLS]] — Certificate setup
- [[docs/deployment/workers|Workers]] — Tuning worker count
- [[docs/deployment/security|Security]] — Proxy headers, request smuggling prevention
- [[docs/deployment/observability|Observability]] — Health checks, request IDs, metrics
