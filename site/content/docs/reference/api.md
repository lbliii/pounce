---
title: API Reference
description: Public API for the pounce package
draft: false
weight: 10
lang: en
type: doc
tags: [api, reference, types]
keywords: [api, run, serverconfig, asgiapp, scope, receive, send]
category: reference
---

## Public API

The `pounce` package exports the following:

```python
from pounce import (
    run, ServerConfig, ASGIApp, Scope, Receive, Send,
    CORSMiddleware, SecurityHeadersMiddleware, Response,
    StaticFiles, create_static_handler,
    PounceError, LifespanError, ReloadError, SupervisorError, TLSError,
    # Lifecycle events (new in 0.5)
    BufferedCollector, LoggingCollector, NoopCollector,
    ConnectionOpened, RequestStarted, ResponseCompleted,
    ClientDisconnected, ConnectionCompleted,
    LifecycleCollector, LifecycleEvent,
)
```

## `pounce.run()`

Start a pounce server.

```python
def run(app: str | ASGIApp, **kwargs: Unpack[ServerConfigKwargs]) -> None:
```

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `app` | `str \| ASGIApp` | ASGI application import string (e.g., `"myapp:app"`) or an ASGI callable |
| `**kwargs` | `ServerConfigKwargs` | Configuration overrides passed to `ServerConfig` |

**Example:**

```python
import pounce

# Minimal
pounce.run("myapp:app")

# Configured
pounce.run(
    "myapp:app",
    host="0.0.0.0",
    port=8000,
    workers=4,
    compression=True,
    server_timing=True,
)
```

## `pounce.ServerConfig`

Frozen dataclass holding all server configuration. See [[docs/configuration/server-config|ServerConfig]] for the complete field reference.

```python
from pounce import ServerConfig

config = ServerConfig(host="0.0.0.0", port=8000, workers=4)
print(config.resolve_workers())  # 4
```

## ASGI Types

Pounce exports typed definitions for the ASGI interface:

### `ASGIApp`

```python
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
```

The standard ASGI application callable.

### `Scope`

```python
Scope = MutableMapping[str, Any]
```

The ASGI connection scope dictionary.

### `Receive`

```python
Receive = Callable[[], Awaitable[dict[str, Any]]]
```

Callable that receives ASGI events from the client.

### `Send`

```python
Send = Callable[[dict[str, Any]], Awaitable[None]]
```

Callable that sends ASGI events to the client.

## `__version__`

```python
from pounce import __version__

print(__version__)  # e.g., "0.2.0"
```

The installed package version, read from `importlib.metadata`.

## Middleware

### `CORSMiddleware`

Built-in CORS middleware that appends `Access-Control-*` response headers to every response (a post-response hook). It does not intercept or answer `OPTIONS` preflight requests — those still reach your app.

```python
from pounce import CORSMiddleware, ServerConfig

cors = CORSMiddleware(
    allow_origin="https://example.com",
    allow_methods="GET, POST",
    allow_headers="Authorization",
    max_age=3600,
)
config = ServerConfig(middleware=[cors])
```

### `SecurityHeadersMiddleware`

Injects security headers (`X-Content-Type-Options`, `X-Frame-Options`, etc.).

```python
from pounce import SecurityHeadersMiddleware, ServerConfig

config = ServerConfig(middleware=[SecurityHeadersMiddleware()])
```

### `Response`

Simple response object for middleware use.

## Static Files

### `StaticFiles`

Class for static file serving configuration.

### `create_static_handler()`

Factory function to create a static file handler from config.

## Error Classes

All errors inherit from `PounceError`. See [[docs/reference/errors|Error Reference]] for the full hierarchy.

| Class | Status Code | Description |
|-------|-------------|-------------|
| `PounceError` | 500 | Base exception |
| `LifespanError` | 500 | ASGI lifespan startup/shutdown failure |
| `SupervisorError` | 500 | Worker spawn/crash failure |
| `TLSError` | 500 | TLS configuration/handshake failure |
| `ReloadError` | 500 | File-watcher/reload failure |

## Lifecycle Events

Pounce exports typed, frozen lifecycle events for framework-level observability. Events cross thread boundaries safely with zero copying or locking.

```python
from pounce import (
    BufferedCollector,
    ConnectionOpened,
    RequestStarted,
    ResponseCompleted,
    ClientDisconnected,
    ConnectionCompleted,
    LifecycleCollector,
    LoggingCollector,
    NoopCollector,
)
```

### Event Types

| Event | Fields | When |
|-------|--------|------|
| `ConnectionOpened` | connection_id, worker_id, client_addr, client_port, server_addr, server_port, protocol, timestamp_ns | TCP connection accepted |
| `RequestStarted` | connection_id, worker_id, method, path, http_version, timestamp_ns | Request head parsed |
| `ResponseCompleted` | connection_id, worker_id, status, bytes_sent, duration_ms, timestamp_ns | Response fully sent |
| `ClientDisconnected` | connection_id, worker_id, during_streaming, timestamp_ns | Client closed unexpectedly |
| `ConnectionCompleted` | connection_id, worker_id, requests_served, total_bytes_sent, duration_ms, reason, timestamp_ns | TCP connection closed |

### Collectors

| Collector | Purpose |
|-----------|---------|
| `NoopCollector` | Default. Discards all events (zero overhead) |
| `BufferedCollector` | Thread-safe buffer with auto-flush and batch callbacks |
| `LoggingCollector` | Structured JSON logging with slow-request detection |

### Example: Custom Collector

```python
from pounce import BufferedCollector, ResponseCompleted

def on_batch(events):
    for event in events:
        if isinstance(event, ResponseCompleted) and event.duration_ms > 1000:
            print(f"Slow request: {event.duration_ms:.0f}ms")

collector = BufferedCollector(max_buffer_size=100, on_flush=on_batch)
```

### `LifecycleCollector` Protocol

Implement this protocol to create custom collectors:

```python
from pounce import LifecycleCollector, LifecycleEvent

class MyCollector:
    def record(self, event: LifecycleEvent) -> None:
        # Must be thread-safe
        ...
```

## See Also

- [[docs/configuration/server-config|ServerConfig]] — Full configuration reference
- [[docs/extending/asgi-bridge|ASGI Bridge]] — Bridge internals
