---
title: Pounce
description: A Python ASGI server for deployments, streaming responses, and free-threaded Python
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

**Pure Python. Frozen config. True parallelism.**

Pounce is a pure-Python ASGI server for Python 3.14t with a low-overhead
HTTP/1.1 fast path, a frozen shared `ServerConfig`, and thread-worker reload
draining. No C extensions in the server core.

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
True OS thread parallelism on Python 3.14t. N workers share one interpreter with
a frozen server configuration object.
:::{/card}

:::{card} Fast-Path Parsing
:icon: zap
Built-in HTTP/1.1 parser for sync workers covers method validation, header size
limits, duplicate Content-Length, and Content-Length/Transfer-Encoding
ambiguity. Pure Python.
:::{/card}

:::{card} Thread-Worker Reload
:icon: refresh-cw
Rolling restart in thread-worker mode spawns a new worker generation while
draining the old. Other worker modes document their own lifecycle limits.
:::{/card}

:::{card} Observable by Default
:icon: activity
Typed lifecycle events, Prometheus /metrics, OpenTelemetry tracing, and Server-Timing headers. Subscribe to structured events from your framework code.
:::{/card}

:::{/cards}

## Common Use Cases

- Running standard ASGI apps with a Python-native server
- Replacing Uvicorn deployments while keeping a familiar CLI
- Serving streaming responses with low buffering overhead
- Deploying free-threaded Python apps with shared-memory worker threads
- Running `pounce bench` to measure and compare server performance
- Building framework-level observability on typed lifecycle events

---

## Protocols

| Protocol | Backend | Install |
|----------|---------|---------|
| HTTP/1.1 | h11 (pure Python) | built-in |
| HTTP/2 | h2 (stream multiplexing, priority) | `bengal-pounce[h2]` |
| WebSocket | wsproto (HTTP/1 WebSocket; WS-over-H2 also requires h2) | `bengal-pounce[ws]` |
| TLS | stdlib ssl + truststore | `bengal-pounce[tls]` |
| HTTP/3 | bengal-zoomies (QUIC/UDP) | `bengal-pounce[h3]` |
| All | Everything above | `bengal-pounce[full]` |

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
