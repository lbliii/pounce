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
pounce serve --app APP [OPTIONS]
```

The `--app` argument is a Python module path with an attribute, e.g. `myapp:app`. The app factory pattern is also supported: `myapp:create_app()`.

## Options

### Server

| Flag | Default | Description |
|------|---------|-------------|
| `--host TEXT` | `127.0.0.1` | Bind address |
| `--port INT` | `8000` | Bind port |
| `--uds PATH` | — | Unix domain socket path (mutually exclusive with `--host`/`--port`) |
| `--workers INT` | `1` | Number of workers (0 = auto-detect from CPU cores) |
| `--worker-mode TEXT` | `auto` | Worker execution model: `auto` (sync on 3.14t, async on GIL), `sync` (blocking I/O), `async` (event loop), or `subinterpreter` |
| `--cpu-affinity` | `disabled` | Pin each worker to a CPU core (Linux only, reduces cache thrashing) |

### Timeouts

| Flag | Default | Description |
|------|---------|-------------|
| `--keep-alive-timeout FLOAT` | `5.0` | Keep-alive timeout (seconds) |
| `--header-timeout FLOAT` | `10.0` | Max seconds to receive complete request headers (slowloris protection) |
| `--request-timeout FLOAT` | `30.0` | Max seconds to receive a complete request body |
| `--shutdown-timeout FLOAT` | `10.0` | Shutdown grace period per worker (seconds, parallel joins) |

::::{note}
`max_request_size`, `max_connections`, and `backlog` are available only via `ServerConfig` — they are not exposed as CLI flags.
::::

### Logging

| Flag | Default | Description |
|------|---------|-------------|
| `--log-level TEXT` | `info` | Log level (debug/info/warning/error/critical) |
| `--log-format TEXT` | `auto` | Log output format: `auto` (pretty on TTY, JSON when piped), `text`, or `json` |
| `--no-access-log` | — | Disable access logging |

::::{tip}
5xx responses are logged at `WARNING` level (instead of `INFO`) so they stand out visually and can be filtered separately.

When `--log-format json` is set, each access-log line is emitted as a flat
structured JSON object on stderr:

```json
{"ts": "2026-02-08T12:00:00+00:00", "level": "warn", "method": "GET", "path": "/", "status": 500, "bytes": 21, "duration_ms": 98.9, "client": "127.0.0.1:5000", "req_id": "a1b2c3d4e5f67890a1b2c3d4e5f67890", "worker": 0}
```

The field set, types, and the `req_id` policy are a stability contract — see
[the access-log schema](../deployment/observability.md#json-access-log-schema).
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
| `--reload-include TEXT` | — | Extra file extensions to watch beyond the default set (comma-separated, e.g. `".rst,.scss"`) |
| `--reload-dir PATH` | — | Extra directory to watch (repeatable) |

The default reload watch set covers Python and config sources plus the common
static-site authoring files:

`.py`, `.pyi`, `.yaml`, `.yml`, `.toml`, `.json`, `.cfg`, `.ini`, `.md`, `.html`, `.css`, `.js`, `.svg`

Editing a `.md`, `.html`, or `.css` file under a watched directory triggers a
reload without `--reload-include`. The watcher scans the current working
directory, any `--reload-dir` paths, and any configured static-mount
directories. Assets served from outside those locations need an explicit
`--reload-dir`. Use `--reload-include` only for extensions outside the default
set.

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
pounce serve --app myapp:app --reload --log-level debug

# Development: .md/.html/.css/.js/.svg are watched by default under --reload
pounce serve --app myapp:app --reload --reload-dir ./templates

# Watch additional extensions beyond the default set
pounce serve --app myapp:app --reload --reload-include ".rst,.scss"

# Production (TCP)
pounce serve --app myapp:app --host 0.0.0.0 --workers 0 --no-access-log

# Production with JSON logs (for log aggregation)
pounce serve --app myapp:app --host 0.0.0.0 --workers 0 --log-format json

# Production with Unix domain socket (behind nginx/caddy)
pounce serve --app myapp:app --uds /run/pounce.sock --workers 0

# Production with health checks and slowloris protection
pounce serve --app myapp:app \
    --host 0.0.0.0 \
    --workers 0 \
    --health-check-path /health \
    --header-timeout 10 \
    --log-format json

# TLS
pounce serve --app myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem

# TLS with HTTP/3
pounce serve --app myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem --http3

# Full production configuration
pounce serve --app myapp:app \
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
