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
from pounce import run, ServerConfig, ASGIApp, Scope, Receive, Send
```

## `pounce.run()`

Start a pounce server.

```python
def run(app: str, **kwargs: Unpack[ServerConfigKwargs]) -> None:
```

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `app` | `str` | ASGI application string (e.g., `"myapp:app"` or `"myapp:create_app()"`) |
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

## See Also

- [[docs/configuration/server-config|ServerConfig]] — Full configuration reference
- [[docs/extending/asgi-bridge|ASGI Bridge]] — Bridge internals
