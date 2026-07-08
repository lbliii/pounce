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

The sync worker hot path uses a custom parser measured around ~3 µs/req on the
local parser microbenchmark, with tested safety checks:

- Method-token validation (rejects malformed tokens; valid extension methods pass through)
- Header size limits (16 KB, prevents exhaustion)
- Null byte and control character injection detection
- Duplicate Content-Length detection (request smuggling prevention)
- Content-Length + Transfer-Encoding conflict detection

## Extension Methods and `QUERY`

Pounce preserves syntactically valid HTTP method tokens instead of maintaining
an allowlist. In particular, `QUERY` requests and their bodies reach the ASGI
application with `scope["method"] == "QUERY"` on HTTP/1.1, HTTP/2, and HTTP/3.
This behavior is tested through each protocol path.

`QUERY` remains defined by an
[active IETF Internet-Draft](https://datatracker.ietf.org/doc/draft-ietf-httpbis-safe-method-w-body/),
not a published RFC. Pounce supplies transport and ASGI forwarding only: the
application owns query semantics, media-type validation, caching, authorization,
and any `Accept-Query` response field. Intermediaries in front of Pounce must
also be configured to pass the method and request body.

## Keep-Alive

Pounce reuses TCP connections between requests (HTTP/1.1 keep-alive). The timeout is configurable:

```python
pounce.run("myapp:app", keep_alive_timeout=5.0)  # seconds
```

After the timeout, idle connections are closed. Set `max_requests_per_connection` to limit requests per connection (0 = unlimited).

## Chunked Transfer

Pounce handles chunked transfer encoding transparently. When your ASGI app sends multiple `http.response.body` events with `more_body=True`, Pounce uses chunked encoding automatically.

## Expect: 100-continue

When a client sends an `Expect: 100-continue` request header, it withholds the
request body until the server acknowledges with an interim `100 Continue`
status line. Pounce emits that interim response before reading the body on both
the async (h11) and sync (fast parser) HTTP/1.1 worker paths, so clients that
wait for it do not stall. This is HTTP/1.1 only; HTTP/2 and HTTP/3 use their own
flow-control mechanisms instead.

## Trailers

HTTP/1.1 trailing headers (sent after a chunked body) are not supported. Pounce
does not surface request trailers to the ASGI app and does not emit response
trailers. Applications that rely on trailers should not depend on them with
Pounce.

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
