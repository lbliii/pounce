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
| `--workers INT` | `1` | Number of workers (0 = auto-detect) |
| `--backlog INT` | `2048` | Socket listen backlog |

### Timeouts

| Flag | Default | Description |
|------|---------|-------------|
| `--keep-alive-timeout FLOAT` | `5.0` | Keep-alive timeout (seconds) |
| `--request-timeout FLOAT` | `30.0` | Request timeout (seconds) |
| `--shutdown-timeout FLOAT` | `10.0` | Shutdown grace period (seconds) |

### Limits

| Flag | Default | Description |
|------|---------|-------------|
| `--max-request-size INT` | `1048576` | Max request body (bytes) |
| `--max-connections INT` | `10000` | Max concurrent connections |

### Logging

| Flag | Default | Description |
|------|---------|-------------|
| `--log-level TEXT` | `info` | Log level (debug/info/warning/error/critical) |
| `--no-access-log` | — | Disable access logging |

### Features

| Flag | Default | Description |
|------|---------|-------------|
| `--compression / --no-compression` | `enabled` | Toggle content-encoding |
| `--server-timing` | `disabled` | Enable Server-Timing header |
| `--reload` | `disabled` | Watch files and restart on changes |

### TLS

| Flag | Default | Description |
|------|---------|-------------|
| `--ssl-certfile PATH` | — | TLS certificate file |
| `--ssl-keyfile PATH` | — | TLS private key file |

### Other

| Flag | Default | Description |
|------|---------|-------------|
| `--root-path TEXT` | `""` | ASGI root_path for reverse proxies |
| `--server-header TEXT` | `pounce` | Server response header value |

## Examples

```bash
# Development
pounce myapp:app --reload --log-level debug

# Production
pounce myapp:app --host 0.0.0.0 --workers 0 --no-access-log

# TLS
pounce myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem

# Full configuration
pounce myapp:app \
    --host 0.0.0.0 \
    --port 443 \
    --workers 4 \
    --ssl-certfile cert.pem \
    --ssl-keyfile key.pem \
    --compression \
    --server-timing \
    --log-level warning
```

## See Also

- [[docs/configuration/server-config|ServerConfig]] — Programmatic configuration
- [[docs/get-started/quickstart|Quickstart]] — Getting started guide
