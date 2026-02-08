---
title: HTTP/1.1
description: HTTP/1.1 protocol handling via h11 or httptools
draft: false
weight: 10
lang: en
type: doc
tags: [http, h11, httptools, protocol]
keywords: [http, http1, h11, httptools, keep-alive, chunked]
category: reference
---

## Overview

HTTP/1.1 is Pounce's default and only required protocol. Two backends are available:

| Backend | Type | Install | Best For |
|---------|------|---------|----------|
| h11 | Pure Python | Built-in | Debugging, portability |
| httptools | C extension | `pounce[fast]` | Maximum throughput |

Pounce auto-detects httptools at import time. If installed, it's used automatically.

## h11 Backend

The default backend. Pure Python, no compilation needed:

```python
import pounce

# Uses h11 by default
pounce.run("myapp:app")
```

h11 is a state-machine-based HTTP/1.1 parser. It's well-tested, handles edge cases correctly, and is easy to debug.

## httptools Backend

For higher throughput, install the httptools extra:

```bash
uv add "bengal-pounce[fast]"
```

httptools wraps the Node.js HTTP parser (llhttp) in a Python C extension. It's significantly faster for parsing but adds a compiled dependency.

## Keep-Alive

Pounce reuses TCP connections between requests (HTTP/1.1 keep-alive). The timeout is configurable:

```python
pounce.run("myapp:app", keep_alive_timeout=5.0)  # seconds
```

After the timeout, idle connections are closed. Set `max_requests_per_connection` to limit requests per connection (0 = unlimited).

## Chunked Transfer

Pounce handles chunked transfer encoding transparently. When your ASGI app sends multiple `http.response.body` events with `more_body=True`, Pounce uses chunked encoding automatically.

## Limits

| Setting | Default | Description |
|---------|---------|-------------|
| `max_request_size` | 1 MB | Maximum request body size |
| `max_header_size` | 64 KB | Maximum total header size |
| `max_headers` | 100 | Maximum number of headers |
| `h11_max_incomplete_event_size` | 16 KB | h11 parser buffer limit |

## See Also

- [[docs/protocols/http2|HTTP/2]] — Stream multiplexing and header compression
- [[docs/configuration/server-config|ServerConfig]] — All configuration options
