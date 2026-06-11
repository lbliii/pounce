---
title: FAQ
description: Frequently asked questions about Pounce
draft: false
weight: 50
lang: en
type: doc
tags: [faq, questions]
keywords: [faq, questions, answers, help]
category: explanation
---

## General

### What is Pounce?

Pounce is a free-threading-native ASGI server for Python 3.14t. It serves ASGI applications using real OS threads sharing a single interpreter, rather than the traditional fork-based model.

### Does Pounce work on standard (GIL) Python?

Yes. On GIL builds, Pounce automatically uses processes instead of threads. The API and configuration are identical — the supervisor detects the runtime via `sys._is_gil_enabled()` and adapts.

### What Python version do I need?

Python 3.14 or later. For thread-based workers (the primary use case), you need a free-threading build (Python 3.14t).

### What's the current status?

Pounce is in beta. HTTP/1.1 is core; HTTP/2, HTTP/3, and WebSocket are optional
extras with their own install and proof boundaries. TLS, multi-worker serving,
compression, static files, middleware, rate limiting, OpenTelemetry, Prometheus,
and streaming are available as optional server features or integrations. It's a
young project, so expect rough edges and check the feature-specific docs before
treating a path as production-critical.

## Compatibility

### Does Pounce work with FastAPI / Starlette / Django?

Yes. Pounce serves any standard ASGI application. If your framework produces a valid ASGI callable, Pounce can serve it.

### Can I use Pounce as a drop-in replacement for Uvicorn?

For most use cases, yes. The CLI syntax is similar:

```bash
# Uvicorn
uvicorn myapp:app --host 0.0.0.0 --port 8000 --workers 4

# Pounce
pounce serve --app myapp:app --host 0.0.0.0 --port 8000 --workers 4
```

See [[docs/tutorials/migrate-from-uvicorn|Migrate from Uvicorn]] for details.

### Does Pounce support ASGI lifespan?

Yes. Pounce sends `lifespan.startup` and `lifespan.shutdown` events per the ASGI specification.

## Technical

### Why threads instead of processes?

On Python 3.14t, the GIL is removed. Threads can execute Python code in true parallel. Threads sharing one interpreter means:

- **One copy of the app** — not N copies
- **Shared frozen configuration** — one `ServerConfig` object, with
  per-request mutable state kept separate
- **No IPC overhead** — workers communicate through memory, not pipes

### What's the overhead of Pounce vs raw asyncio?

Minimal. The request pipeline is: parse → scope → app → serialize → write. The ASGI bridge is per-request with no shared mutable state. The dominant cost is your application, not the server.

### How does compression work?

Pounce negotiates content-encoding via `Accept-Encoding`. Priority: zstd > gzip > identity. Zstd uses Python 3.14's stdlib `compression.zstd` (PEP 784) — zero external dependencies.

### What is Server-Timing?

A W3C standard that surfaces server-side latency in browser DevTools. When enabled (`--server-timing`), Pounce injects parse/app/encode timings into every response header.

## Part of Bengal

### Do I need other Bengal packages to use Pounce?

No. Pounce works with any ASGI framework. It's part of the Bengal ecosystem but fully standalone. See [[docs/about/ecosystem|Ecosystem]] for the full picture.
