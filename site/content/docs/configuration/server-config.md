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

`ServerConfig` is a frozen dataclass (`@dataclass(frozen=True, slots=True)`) that holds all server settings. It's created once at startup and shared across all workers — safe because it's immutable.

```python
from pounce import ServerConfig

config = ServerConfig(
    host="0.0.0.0",
    port=8000,
    workers=4,
    compression=True,
    server_timing=True,
)
```

## Fields

### Bind Address

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | `str` | `"127.0.0.1"` | Bind address |
| `port` | `int` | `8000` | Bind port (0-65535) |

### Workers

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `workers` | `int` | `1` | Worker count. 0 = auto-detect from CPU cores. 1 = single-worker (no supervisor). 2+ = multi-worker with supervisor. |
| `backlog` | `int` | `2048` | Socket listen backlog |

### Timeouts

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `keep_alive_timeout` | `float` | `5.0` | Seconds to keep idle connections open |
| `request_timeout` | `float` | `30.0` | Maximum seconds for a complete request |
| `shutdown_timeout` | `float` | `10.0` | Seconds to wait for in-flight requests during shutdown |

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

### Development

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `reload` | `bool` | `False` | Watch source files and restart workers on changes |

### Protocol Tuning

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `h11_max_incomplete_event_size` | `int \| None` | `None` | h11 parser buffer limit (None = h11 default 16 KB) |

### Security

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `trusted_hosts` | `tuple[str, ...]` | `()` | Trusted proxy hosts (empty = direct connection) |

### TLS

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ssl_certfile` | `str \| None` | `None` | Path to TLS certificate file |
| `ssl_keyfile` | `str \| None` | `None` | Path to TLS private key file |

:::{note}
`ssl_certfile` and `ssl_keyfile` must both be set or both be `None`. Setting only one raises `ValueError`.
:::{/note}

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
