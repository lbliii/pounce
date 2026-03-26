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

Built-in CORS middleware for cross-origin request handling.

```python
from pounce import CORSMiddleware, ServerConfig

cors = CORSMiddleware(
    allow_origins=["https://example.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization"],
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

## See Also

- [[docs/configuration/server-config|ServerConfig]] — Full configuration reference
- [[docs/extending/asgi-bridge|ASGI Bridge]] — Bridge internals
