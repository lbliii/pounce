---
title: When to Use Pounce
description: When Pounce fits, how it differs from process-based ASGI servers, and when to consider alternatives
draft: false
weight: 40
lang: en
type: doc
tags: [architecture, deployment]
keywords: [python asgi server, uvicorn alternative, architecture, deployment, free-threading]
category: explanation
---

# When to Use Pounce

Pounce is built for Python 3.14t and the free-threading model. If you are evaluating
Python ASGI servers, the main distinction is its worker model: threads on 3.14t,
automatic process fallback on GIL builds.

## Pounce's Model

- **Thread-based parallelism** — N worker threads share one interpreter, one copy of your app
- **Shared memory** — Lower memory footprint than process-based workers
- **Streaming-first** — Body chunks sent immediately to socket
- **Pure Python** — One dependency (h11). Debuggable, hackable, readable
- **Optional extras** — HTTP/2, WebSocket, TLS, HTTP/3 via `pounce[h2]`, `pounce[ws]`, `pounce[tls]`, `pounce[h3]`

## When Pounce Fits

- You're on **Python 3.14t** and want thread-based parallelism
- You want **shared memory** across workers (lower memory footprint)
- You need **streaming responses** with minimal latency
- You want **stdlib compression** (zstd) without external dependencies
- You prefer **pure Python** for debuggability and extensibility
- You want a **Uvicorn-like CLI** with a different concurrency model

## When to Consider Alternatives

- **Uvicorn** — Mature ecosystem, C-based HTTP parser (httptools/uvloop), broad production history. Better if you need Python < 3.14, prefer battle-tested stability, or depend on uvloop for I/O performance.
- **Granian** — Rust-based I/O via Hyper/Tokio with higher raw throughput on simple endpoints (~3x Uvicorn on empty responses). Also supports free-threaded Python since v2.0. Better if you need maximum requests-per-second and don't need HTTP/3, built-in compression, or middleware.
- **Hypercorn** — Supports HTTP/2 without TLS (h2c) and trio/asyncio backends. Better if you need non-asyncio event loops or cleartext HTTP/2.
- **Existing deployments** — If your current setup works and you're not on 3.14t, there's no urgent reason to switch.

## See Also

- [[docs/about/performance|Performance]] — Benchmarks and design
- [[docs/about/thread-safety|Thread Safety]] — The shared memory model
