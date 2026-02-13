---
title: Pounce
description: A free-threading-native ASGI server for Python 3.14t
template: home.html
weight: 100
type: page
draft: false
lang: en
keywords: [pounce, asgi, server, python, free-threading, nogil, http2, websocket]
category: home

# Hero configuration
blob_background: true

# CTA Buttons
cta_buttons:
  - text: Get Started
    url: /docs/get-started/
    style: primary
  - text: API Reference
    url: /docs/reference/
    style: secondary

show_recent_posts: false
---

## ASGI, Without the GIL

**Free-threading native. Streaming-first. Pure Python.**

Pounce is a pure-Python ASGI server designed from scratch for Python 3.14t. Instead of fork-based worker models, pounce runs N worker threads sharing a single interpreter — leveraging free-threading for true parallelism without memory duplication.

```python
import pounce

pounce.run("myapp:app")
```

---

## Why Pounce?

:::{cards}
:columns: 2
:gap: medium

:::{card} Free-Threading Native
:icon: cpu
Real OS threads, not processes. N workers share one interpreter, one copy of the application, one set of frozen config — zero synchronization for immutable data.
:::{/card}

:::{card} Streaming-First
:icon: zap
The response pipeline sends body chunks immediately to the socket. Chunked HTML, event streams, AI token delivery — no buffering, instant delivery.
:::{/card}

:::{card} 2026-Native Features
:icon: package
First ASGI server with zero-dependency zstd compression via Python 3.14's stdlib (PEP 784). Server-Timing headers auto-injected for built-in observability.
:::{/card}

:::{card} Pure Python
:icon: code
No Rust, no C extensions in the server core. One dependency (h11). Debuggable, hackable, readable. Optional extras for HTTP/2, WebSocket, and TLS.
:::{/card}

:::{/cards}

---

## Quick Comparison

| Feature | Pounce | Uvicorn | Granian | Hypercorn |
|---------|--------|---------|---------|-----------|
| **Parallelism** | Threads (nogil) | Processes (fork) | Rust I/O + Python | Processes |
| **Memory model** | Shared (1 copy) | Duplicated (N copies) | Mixed | Duplicated |
| **Free-threading** | Native | Compatible | Partial | No |
| **HTTP/2** | Optional (h2) | No | Yes | Yes |
| **WebSocket** | Optional (wsproto) | Optional | Yes | Yes |
| **Compression** | zstd + gzip (stdlib) | No | No | No |
| **Server-Timing** | Built-in | No | No | No |
| **Dependencies** | 1 (h11) | 2+ | Rust binary | 3+ |

---

## Protocols

| Protocol | Backend | Install |
|----------|---------|---------|
| HTTP/1.1 | h11 (pure Python, default) | built-in |
| HTTP/1.1 | httptools (C-accelerated) | `pounce[fast]` |
| HTTP/2 | h2 (stream multiplexing, priority) | `pounce[h2]` |
| WebSocket | wsproto (including WS over H2) | `pounce[ws]` |
| TLS | stdlib ssl + truststore | `pounce[tls]` |
| All | Everything above (except httptools) | `pounce[full]` |

---

## The Bengal Ecosystem

A structured reactive stack — every layer written in pure Python for 3.14t free-threading.

| | | | |
|--:|---|---|---|
| **ᓚᘏᗢ** | [Bengal](https://github.com/lbliii/bengal) | Static site generator | [Docs](https://lbliii.github.io/bengal/) |
| **∿∿** | [Purr](https://github.com/lbliii/purr) | Content runtime | — |
| **⌁⌁** | [Chirp](https://github.com/lbliii/chirp) | Web framework | [Docs](https://lbliii.github.io/chirp/) |
| **=^..^=** | **Pounce** | ASGI server ← You are here | [Docs](https://lbliii.github.io/pounce/) |
| **)彡** | [Kida](https://github.com/lbliii/kida) | Template engine | [Docs](https://lbliii.github.io/kida/) |
| **ฅᨐฅ** | [Patitas](https://github.com/lbliii/patitas) | Markdown parser | [Docs](https://lbliii.github.io/patitas/) |
| **⌾⌾⌾** | [Rosettes](https://github.com/lbliii/rosettes) | Syntax highlighter | [Docs](https://lbliii.github.io/rosettes/) |

Python-native. Free-threading ready. No npm required.
