---
title: Thread Safety
description: How Pounce handles shared state across workers on free-threading builds
draft: false
weight: 30
lang: en
type: doc
tags: [thread-safety, free-threading, nogil]
keywords: [thread-safety, free-threading, nogil, frozen, immutable, shared state]
category: explanation
---

## The Free-Threading Model

Python 3.14t (PEP 703) removes the Global Interpreter Lock. For the first time, Python threads execute in true parallel. This changes the rules for server design:

- **GIL builds**: Multi-worker means multi-process (fork). Each process has its own memory space. Thread safety is irrelevant between workers.
- **Free-threading builds**: Multi-worker means multi-thread. All workers share one memory space. Thread safety matters.

Pounce is designed for the free-threading world. The key principle: **shared data is immutable, mutable data is per-request**.

## What's Shared (Immutable)

These are shared across all worker threads with zero synchronization:

| Data | Type | Why It's Safe |
|------|------|---------------|
| `ServerConfig` | `@dataclass(frozen=True)` | Immutable after creation |
| Application reference | Function/class | Read-only reference |
| Socket objects | OS-managed | Kernel handles concurrent accept |
| Protocol constants | Module-level `frozenset`/`tuple` | Immutable collections |

`ServerConfig` uses `frozen=True` and `slots=True` — any attempt to mutate it raises `FrozenInstanceError`.

## What's Per-Request (Mutable)

These are created fresh for each request and never shared:

| Data | Scope | Lifecycle |
|------|-------|-----------|
| ASGI scope dict | Per-request | Created at parse, GC'd after response |
| `receive` callable | Per-request | Bound to connection reader |
| `send` callable | Per-request | Bound to connection writer |
| Protocol parser state | Per-connection | Reset between requests (keep-alive) |
| Compression state | Per-response | Created per-response |
| Server-Timing metrics | Per-request | Injected into response headers |

## What's Per-Worker

Each worker has its own:

- **asyncio event loop** — Event loops are not thread-safe; each worker runs its own
- **Connection tracking** — Per-worker connection count for backpressure
- **Logging context** — Worker ID tagged in log output

## Auto-Detection

Pounce detects the runtime mode at startup via `sys._is_gil_enabled()`:

```python
# On Python 3.14t (free-threading):
#   GIL disabled → workers are threads

# On GIL builds (standard CPython):
#   GIL enabled → workers are processes
```

The supervisor adapts automatically. Your code and config stay the same.

## Guidelines for ASGI Apps

Pounce handles thread safety for its own internals. For your ASGI application:

:::{tip}
If your app works correctly with Uvicorn's multi-process mode, it will work with Pounce's thread mode — the isolation model is the same (per-request scope, no shared mutable state in the framework bridge).
:::{/tip}

:::{warning}
If your app uses global mutable state (module-level dicts, caches without locks), you'll need to add synchronization for free-threading builds. This is a Python-wide concern, not Pounce-specific.
:::{/warning}

## See Also

- [[docs/about/architecture|Architecture]] — Server layer design
- [[docs/about/comparison|Comparison]] — How other servers handle threading
- [[docs/deployment/workers|Workers]] — Configuring worker count and mode
