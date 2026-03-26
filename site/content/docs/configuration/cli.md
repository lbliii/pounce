---
title: CLI Reference
description: Command-line options for the pounce command
draft: false
weight: 20
lang: en
type: doc
tags: [cli, command-line, reference]
keywords: [cli, command-line, pounce, arguments, flags]
category: reference
---

## Usage

```bash
pounce APP [OPTIONS]
```

The `APP` argument is a Python module path with an attribute, e.g. `myapp:app`. The app factory pattern is also supported: `myapp:create_app()`.

## Options

### Server

| Flag | Default | Description |
|------|---------|-------------|
| `--host TEXT` | `127.0.0.1` | Bind address |
| `--port INT` | `8000` | Bind port |
| `--uds PATH` | — | Unix domain socket path (mutually exclusive with `--host`/`--port`) |
| `--workers INT` | `1` | Number of workers (0 = auto-detect from CPU cores) |
| `--worker-mode TEXT` | `auto` | Worker execution model: `auto` (sync on 3.14t, async on GIL), `sync` (blocking I/O), `async` (event loop) |
| `--cpu-affinity` | `disabled` | Pin each worker to a CPU core (Linux only, reduces cache thrashing) |

### Timeouts

| Flag | Default | Description |
|------|---------|-------------|
| `--keep-alive-timeout FLOAT` | `5.0` | Keep-alive timeout (seconds) |
| `--header-timeout FLOAT` | `10.0` | Max seconds to receive complete request headers (slowloris protection) |
| `--shutdown-timeout FLOAT` | `10.0` | Shutdown grace period per worker (seconds, parallel joins) |

::::{note}
`request_timeout`, `max_request_size`, `max_connections`, and `backlog` are available only via `ServerConfig` — they are not exposed as CLI flags.
::::

### Logging

| Flag | Default | Description |
|------|---------|-------------|
| `--log-level TEXT` | `info` | Log level (debug/info/warning/error/critical) |
| `--log-format TEXT` | `auto` | Log output format: `auto` (pretty on TTY, JSON when piped), `text`, or `json` |
| `--no-access-log` | — | Disable access logging |

::::{tip}
5xx responses are logged at `WARNING` level (instead of `INFO`) so they stand out visually and can be filtered separately.

When `--log-format json` is set, all log output is emitted as structured JSON:

```json
{"timestamp": "2026-02-08T12:00:00+00:00", "level": "WARNING", "logger": "pounce.access", "method": "GET", "path": "/", "status": 500, "bytes_sent": 21, "duration_ms": 98.9, "client": "127.0.0.1:5000", "request_id": "a1b2c3d4e5f6..."}
```
::::

### Observability

| Flag | Default | Description |
|------|---------|-------------|
| `--health-check-path TEXT` | — | Path for built-in health endpoint (e.g. `/health`). Disabled by default. |

### Features

| Flag | Default | Description |
|------|---------|-------------|
| `--no-compression` | — | Disable content-encoding (compression is enabled by default) |
| `--server-timing` | `disabled` | Enable Server-Timing header |
| `--http3` | `disabled` | Enable HTTP/3 (QUIC/UDP). Requires `--ssl-certfile` and `--ssl-keyfile`. |
| `--reload` | `disabled` | Watch files and restart on changes |
| `--reload-include TEXT` | — | Extra file extensions to watch (comma-separated, e.g. `".html,.css,.md"`) |
| `--reload-dir PATH` | — | Extra directory to watch (repeatable) |

### TLS

| Flag | Default | Description |
|------|---------|-------------|
| `--ssl-certfile PATH` | — | TLS certificate file |
| `--ssl-keyfile PATH` | — | TLS private key file |

### Security

| Flag | Default | Description |
|------|---------|-------------|
| `--max-requests-per-connection INT` | `0` | Max requests per keep-alive connection (0 = unlimited) |

### Other

| Flag | Default | Description |
|------|---------|-------------|
| `--root-path TEXT` | `""` | ASGI root_path for reverse proxies |

## Examples

```bash
# Development
pounce myapp:app --reload --log-level debug

# Development with extra file watching
pounce myapp:app --reload --reload-include ".html,.css,.md" --reload-dir ./templates

# Production (TCP)
pounce myapp:app --host 0.0.0.0 --workers 0 --no-access-log

# Production with JSON logs (for log aggregation)
pounce myapp:app --host 0.0.0.0 --workers 0 --log-format json

# Production with Unix domain socket (behind nginx/caddy)
pounce myapp:app --uds /run/pounce.sock --workers 0

# Production with health checks and slowloris protection
pounce myapp:app \
    --host 0.0.0.0 \
    --workers 0 \
    --health-check-path /health \
    --header-timeout 10 \
    --log-format json

# TLS
pounce myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem

# TLS with HTTP/3
pounce myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem --http3

# Full production configuration
pounce myapp:app \
    --host 0.0.0.0 \
    --port 443 \
    --workers 4 \
    --worker-mode auto \
    --ssl-certfile cert.pem \
    --ssl-keyfile key.pem \
    --no-compression \
    --server-timing \
    --health-check-path /health \
    --header-timeout 10 \
    --log-level warning \
    --log-format json
```

## See Also

- [[docs/configuration/server-config|ServerConfig]] — Programmatic configuration (all options including those not in CLI)
- [[docs/get-started/quickstart|Quickstart]] — Getting started guide
