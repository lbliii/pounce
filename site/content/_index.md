---
title: Pounce
description: A Python ASGI server for production apps, streaming responses, and free-threaded Python
template: home.html
weight: 100
type: page
draft: false
lang: en
keywords: [pounce, python asgi server, uvicorn alternative, free-threading, nogil, streaming, http2, websocket]
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

## Python ASGI Server for Free-Threaded Python

**Production-ready. Streaming-first. Free-threading aware.**

Pounce is a pure-Python ASGI server designed for Python 3.14+ and optimized for
Python 3.14t. Instead of relying solely on fork-based worker models, Pounce can run
worker threads that share a single interpreter and one copy of your application.

```python
import pounce

pounce.run("myapp:app")
```

---

## Why Use Pounce

:::{cards}
:columns: 2
:gap: medium

:::{card} Free-Threading Native
:icon: cpu
Real OS threads, not processes, on Python 3.14t. N workers share one interpreter, one
copy of the application, and one set of frozen configuration.
:::{/card}

:::{card} Streaming-First
:icon: zap
The response pipeline sends body chunks immediately to the socket. Good fit for
chunked HTML, event streams, and token delivery.
:::{/card}

:::{card} 2026-Native Features
:icon: package
Python 3.14 stdlib `compression.zstd` support, plus Server-Timing headers for built-in
observability.
:::{/card}

:::{card} Pure Python
:icon: code
No Rust and no C extensions in the server core. One required dependency (`h11`) with
optional extras for HTTP/2, WebSocket, and TLS.
:::{/card}

:::{/cards}

## Common Use Cases

- Running standard ASGI apps with a Python-native server
- Replacing Uvicorn deployments while keeping a familiar CLI
- Serving streaming responses with low buffering overhead
- Deploying free-threaded Python apps with shared-memory worker threads

---

## Protocols

| Protocol | Backend | Install |
|----------|---------|---------|
| HTTP/1.1 | h11 (pure Python) | built-in |
| HTTP/2 | h2 (stream multiplexing, priority) | `pounce[h2]` |
| WebSocket | wsproto (including WS over H2) | `pounce[ws]` |
| TLS | stdlib ssl + truststore | `pounce[tls]` |
| All | Everything above | `pounce[full]` |

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
