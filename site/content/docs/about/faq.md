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

Pounce is in beta. All four protocols (HTTP/1.1, HTTP/2, HTTP/3, WebSocket) are implemented along with TLS, multi-worker, compression, static files, middleware, rate limiting, OpenTelemetry, Prometheus, and streaming — covered by a comprehensive test suite (1000+ tests). It's a young project, so expect rough edges.

## Compatibility

### Does Pounce work with FastAPI / Starlette / Django?

Yes. Pounce serves any standard ASGI application. If your framework produces a valid ASGI callable, Pounce can serve it.

### Can I use Pounce as a drop-in replacement for Uvicorn?

For most use cases, yes. The CLI syntax is similar:

```bash
# Uvicorn
uvicorn myapp:app --host 0.0.0.0 --port 8000 --workers 4

# Pounce
pounce myapp:app --host 0.0.0.0 --port 8000 --workers 4
```

See [[docs/tutorials/migrate-from-uvicorn|Migrate from Uvicorn]] for details.

### Does Pounce support ASGI lifespan?

Yes. Pounce sends `lifespan.startup` and `lifespan.shutdown` events per the ASGI specification.

## Technical

### Why threads instead of processes?

On Python 3.14t, the GIL is removed. Threads can execute Python code in true parallel. Threads sharing one interpreter means:

- **One copy of the app** — not N copies
- **Shared immutable data** — frozen config, route tables, templates
- **No IPC overhead** — workers communicate through memory, not pipes

### What's the overhead of Pounce vs raw asyncio?

Minimal. The request pipeline is: parse → scope → app → serialize → write. The ASGI bridge is per-request with no shared mutable state. The dominant cost is your application, not the server.

### How does compression work?

Pounce negotiates content-encoding via `Accept-Encoding`. Priority: zstd > gzip > identity. Zstd uses Python 3.14's stdlib `compression.zstd` (PEP 784) — zero external dependencies.

### What is Server-Timing?

A W3C standard that surfaces server-side latency in browser DevTools. When enabled (`--server-timing`), Pounce injects parse/app/encode timings into every response header.

## Part of Bengal

### What is the Bengal ecosystem?

Bengal is a family of Python packages for content and web applications:

```
purr        Content runtime   (connects everything)
pounce      ASGI server       (serves apps)
chirp       Web framework     (serves HTML)
kida        Template engine   (renders HTML)
patitas     Markdown parser   (parses content)
rosettes    Syntax highlighter (highlights code)
bengal      Static site gen   (builds sites)
```

### Do I need other Bengal packages to use Pounce?

No. Pounce works with any ASGI framework. The Bengal ecosystem is modular — use what you need.
