---
title: Thread Safety
description: How Pounce handles shared state across workers on free-threading builds
draft: false
weight: 30
lang: en
type: doc
tags: [thread-safety, free-threading, nogil]
keywords: [thread-safety, free-threading, nogil, frozen, shared state]
category: explanation
---

## The Free-Threading Model

Python 3.14t (PEP 703) removes the Global Interpreter Lock. For the first time, Python threads execute in true parallel. This changes the rules for server design:

- **GIL builds**: Multi-worker means multi-process (fork). Each process has its own memory space. Thread safety is irrelevant between workers.
- **Free-threading builds**: Multi-worker means multi-thread. All workers share one memory space. Thread safety matters.

Pounce is designed for the free-threading world. The key principle: **server-owned shared configuration is frozen, mutable request data is per-request**.

## What's Shared

These are shared across worker threads without mutating the shared object:

| Data | Type | Why It's Safe |
|------|------|---------------|
| `ServerConfig` | `@dataclass(frozen=True)` | Frozen after creation |
| Application reference | Function/class | Shared callable reference |
| Socket objects | OS-managed | Kernel handles concurrent accept |
| Protocol constants | Module-level `frozenset`/`tuple` | Frozen collections |

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

## The Brotli Principle

A single C extension that re-acquires the GIL defeats free-threading for the entire request. Pounce enforces this principle throughout:

- **Brotli excluded** — The `brotli` C extension re-enables the GIL on 3.14t. Pounce uses stdlib `compression.zstd` (PEP 784) instead.
- **h11 preferred** — Pure Python HTTP/1.1 parsing. No httptools (C extension).
- **stdlib compression** — `zlib` (gzip) and `compression.zstd` are both nogil-safe.

When evaluating dependencies for your ASGI app, audit C extensions for GIL behavior on 3.14t. A fast C library that serializes all your threads is slower than a pure Python alternative that runs in parallel.

## Guidelines for ASGI Apps

Pounce handles thread safety for its own internals. For your ASGI application:

:::{tip}
If your app avoids global mutable state (as recommended for multi-process deployments), it will also work with Pounce's thread mode. Per-request state (ASGI scope, receive, send) is always isolated.
:::

:::{warning}
If your app uses global mutable state (module-level dicts, caches without locks), you'll need to add synchronization for free-threading builds. This is a Python-wide concern, not Pounce-specific.
:::

## Code References

| Pattern | File |
|---------|------|
| PEP 703 declaration | [src/pounce/__init__.py](https://github.com/lbliii/pounce/blob/main/src/pounce/__init__.py) |
| GIL detection (worker mode) | [src/pounce/_runtime.py](https://github.com/lbliii/pounce/blob/main/src/pounce/_runtime.py) |
| ServerConfig (frozen) | [src/pounce/config.py](https://github.com/lbliii/pounce/blob/main/src/pounce/config.py) |
| Brotli exclusion (compression) | [src/pounce/_compression.py](https://github.com/lbliii/pounce/blob/main/src/pounce/_compression.py) |
| NoGIL architecture patterns | [docs/nogil-patterns.md](https://github.com/lbliii/pounce/blob/main/docs/nogil-patterns.md) |

## See Also

- [[docs/about/architecture|Architecture]] — Server layer design
- [[docs/about/comparison|When to Use Pounce]] — Architecture and deployment
- [[docs/deployment/workers|Workers]] — Configuring worker count and mode
- [NoGIL Architecture Patterns](https://github.com/lbliii/pounce/blob/main/docs/nogil-patterns.md) — 10 reusable patterns for building free-threading-safe Python infrastructure
