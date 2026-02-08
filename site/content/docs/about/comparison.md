---
title: Comparison
description: Pounce compared to Uvicorn, Granian, Hypercorn, and Daphne
draft: false
weight: 40
lang: en
type: doc
tags: [comparison, uvicorn, granian, hypercorn]
keywords: [comparison, uvicorn, granian, hypercorn, daphne, asgi, server]
category: explanation
---

## ASGI Server Landscape

Every existing ASGI server was built for a Python that had a GIL. Pounce is built for the world that Python 3.14t creates.

## Feature Comparison

| Feature | Pounce | Uvicorn | Granian | Hypercorn | Daphne |
|---------|--------|---------|---------|-----------|--------|
| **Language** | Python | Python | Rust + Python | Python | Python |
| **Parallelism** | Threads (nogil) | Processes | Rust threads | Processes | Processes |
| **Memory model** | Shared | Duplicated | Mixed | Duplicated | Duplicated |
| **Free-threading** | Native | Compatible | Partial | No | No |
| **HTTP/1.1** | h11 / httptools | httptools / h11 | Hyper (Rust) | h11 | Twisted |
| **HTTP/2** | h2 (optional) | No | Yes | h2 | No |
| **WebSocket** | wsproto (optional) | websockets | Yes | wsproto | Twisted |
| **Compression** | zstd + gzip | No | No | No | No |
| **Server-Timing** | Built-in | No | No | No | No |
| **TLS** | stdlib ssl | Yes | Yes | Yes | No |
| **Dev reload** | Built-in | watchfiles | Built-in | No | No |
| **Dependencies** | 1 (h11) | 2+ | Rust binary | 3+ | Twisted |

## Architecture Comparison

### Uvicorn

Uvicorn runs one event loop per process. Parallelism means fork — four workers means four copies of your app, routes, templates, and config in separate memory. It added Python 3.14 *compatibility* but not a free-threading-native worker model.

### Granian

Granian does its I/O in Rust and calls into Python for the ASGI app. It supports nogil threads, but the core server logic isn't Python — you can't debug or extend it from Python.

### Hypercorn

Hypercorn is process-based with no free-threading awareness. It supports HTTP/2 via h2 but doesn't leverage shared memory or thread-based parallelism.

### Daphne

Daphne is built on Twisted. Process-based, no free-threading support, no HTTP/2. Primarily used with Django Channels.

## When to Use Pounce

Pounce is the right choice when:

- You're on **Python 3.14t** and want thread-based parallelism
- You want **shared memory** across workers (lower memory footprint)
- You need **streaming responses** with minimal latency
- You want **stdlib compression** (zstd) without external dependencies
- You prefer **pure Python** for debuggability and extensibility

## When to Use Something Else

- **Maximum raw throughput** — Granian's Rust I/O layer may be faster for pure proxy workloads
- **Django Channels** — Daphne has the deepest Django integration
- **Existing Uvicorn deployment** — If it's working and you're not on 3.14t, there's no urgent reason to switch

## See Also

- [[docs/about/performance|Performance]] — Benchmarks and design
- [[docs/about/thread-safety|Thread Safety]] — The shared memory model
