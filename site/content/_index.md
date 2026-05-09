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

**Pure Python. 7x faster parsing. True parallelism.**

Pounce is a pure-Python ASGI server that parses HTTP requests in 3 microseconds, runs true parallel worker threads on Python 3.14t, and reloads with zero dropped requests. No C extensions. No GIL.

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
True OS thread parallelism on Python 3.14t. N workers share one interpreter with frozen immutable configuration — zero locks, zero contention.
:::{/card}

:::{card} 7x Faster Parsing
:icon: zap
Built-in HTTP/1.1 parser runs at ~3 us/request vs h11's ~22 us. Full safety checks: method validation, request smuggling detection, header size limits. Pure Python.
:::{/card}

:::{card} Zero-Downtime Reload
:icon: refresh-cw
Rolling restart spawns a new worker generation while draining the old. No dropped requests, no connection errors. Kubernetes-grade reliability without a sidecar.
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
| WebSocket | wsproto (including WS over H2) | `bengal-pounce[ws]` |
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
