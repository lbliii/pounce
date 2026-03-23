---
title: HTTP/1.1
description: HTTP/1.1 protocol handling via h11 and built-in fast parser
draft: false
weight: 10
lang: en
type: doc
tags: [http, h11, protocol]
keywords: [http, http1, h11, keep-alive, chunked]
category: reference
---

## Overview

HTTP/1.1 is Pounce's default and only required protocol. Two parsers are used depending on
the worker mode:

| Parser | Type | Worker | Speed |
|--------|------|--------|-------|
| h11 | Pure Python | Async workers | ~22 µs/req |
| Built-in fast parser | Pure Python | Sync workers | ~3 µs/req |

The sync worker hot path automatically uses the fast built-in parser (`_fast_h1.py`) for
simple request-response handling. Complex requests (chunked bodies, trailer headers) fall
through to h11.

## h11 Backend

The async worker parser. Pure Python, no compilation needed:

```python
import pounce

pounce.run("myapp:app")
```

h11 is a state-machine-based HTTP/1.1 parser. It's well-tested, handles edge cases correctly, and is easy to debug.

## Fast Built-in Parser

The sync worker hot path uses a custom parser that provides ~3 µs/req parsing with full
safety checks:

- Method validation (rejects unknown methods)
- Header size limits (16 KB, prevents exhaustion)
- Null byte and control character injection detection
- Duplicate Content-Length detection (request smuggling prevention)
- Content-Length + Transfer-Encoding conflict detection

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
