---
title: Security
description: Built-in security features for production deployments
draft: false
weight: 40
lang: en
type: doc
tags: [security, proxy, headers, hardening]
keywords: [security, proxy-headers, crlf, request-smuggling, slowloris, trusted-hosts, x-forwarded-for]
category: explanation
---

## Overview

Pounce includes several defense-in-depth security measures that operate at the server level — before your ASGI application is invoked. These protections are active by default and require no configuration.

## Proxy Header Validation

When running behind a reverse proxy (nginx, Caddy, etc.), the proxy adds headers like `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host` to communicate the original client's information.

**The problem:** A malicious client can send these headers directly to spoof their IP or protocol. Pounce prevents this by only trusting these headers from known proxy IPs.

### How It Works

| `trusted_hosts` | Behavior |
|---|---|
| `frozenset()` (empty, default) | Strip all `X-Forwarded-*` headers — assume direct connection |
| `frozenset({"10.0.0.1"})` | Trust proxy at `10.0.0.1` — apply forwarded headers to ASGI scope |
| `frozenset({"10.0.0.1", "10.0.0.2"})` | Trust multiple proxies |
| `frozenset({"*"})` | Trust all peers (only safe for private networks) |

Tuples and lists are also accepted and normalized to `frozenset` internally.

When a trusted proxy sends `X-Forwarded-For`, Pounce updates:

- `scope["client"]` — Set to the real client IP from `X-Forwarded-For`
- `scope["scheme"]` — Set from `X-Forwarded-Proto` (e.g. `"https"`)
- `scope["server"]` and the ASGI `Host` header — Set from `X-Forwarded-Host`,
  including a forwarded port when one is present

This applies consistently to HTTP/1.1, HTTP/2, HTTP/3, and WebSocket scopes so
host-routed applications see the same tenant authority on every protocol path.

For more than one trusted proxy hop, set `forwarded_for_trusted_hops` to the
number of proxies in front of Pounce. Pounce selects the client address from
that position on the right of the `X-Forwarded-For` chain, where trusted
proxies append addresses. The default `1` matches one edge proxy.

When an **untrusted** peer sends these headers, they are silently stripped.

```python
import pounce

pounce.run(
    "myapp:app",
    trusted_hosts=frozenset({"10.0.0.1"}),  # Only trust your proxy
    forwarded_for_trusted_hops=1,
)
```

`X-Real-IP` remains an ordinary request header; Pounce does not use it for
`scope["client"]`. Do not use it for application auth or rate limiting unless
your deployment separately proves that a trusted edge overwrites it.

:::{warning}
Never use `trusted_hosts=("*",)` on internet-facing deployments. Any client could spoof their IP address.
:::

## CRLF Header Injection Protection

Pounce sanitises all response header names and values by stripping carriage return (`\r`) and line feed (`\n`) characters before they reach the HTTP serializer.

This prevents a class of attacks where a malicious ASGI application could inject extra HTTP headers into the response by embedding `\r\n` sequences in header values:

```
X-Safe: clean\r\nX-Injected: evil
```

Without sanitisation, this would appear as two separate headers in the response. Pounce's bridge strips the control characters, and h11 provides an additional validation layer.

This protection applies to both HTTP/1.1 and HTTP/2 responses.

## Request Smuggling Prevention

HTTP request smuggling exploits ambiguities when `Content-Length` and `Transfer-Encoding` headers conflict. Pounce relies on h11's strict HTTP parser, which:

- **Enforces RFC 9112 framing rules** — `Transfer-Encoding` takes precedence over `Content-Length`
- **Rejects duplicate `Content-Length` headers** with different values
- **Rejects obfuscated `Transfer-Encoding`** variants (e.g. `Transfer-Encoding: chunked, identity`)
- **Rejects null bytes** in header values

Because Pounce is not an intermediary (it's the terminal server), desynchronisation attacks are not applicable. The h11 parser's strict mode prevents all known smuggling vectors.

## Slowloris Protection

A slowloris attack holds connections open by sending HTTP headers very slowly — one byte at a time — exhausting the server's connection pool without ever completing a request.

Pounce protects against this with `header_timeout`:

```bash
pounce serve --app myapp:app --header-timeout 10
```

| Timeout | Purpose | Default |
|---|---|---|
| `header_timeout` | Max seconds to receive complete request headers | 10s |
| `keep_alive_timeout` | Max seconds to wait between keep-alive requests | 5s |
| `request_timeout` | Max seconds to wait for the next request-body event | 30s |
| `write_timeout` | Max seconds for blocked H1/H2/WebSocket response delivery | 30s |

The `header_timeout` applies to request headers. After headers complete,
`request_timeout` governs request-body progress. Between completed HTTP
requests, `keep_alive_timeout` applies; it does not reap active HTTP/2 streams,
SSE responses, or accepted WebSockets. `write_timeout` closes a downstream H1,
H2, or WebSocket peer that stops accepting response bytes. HTTP/3 output uses
QUIC's `http3_idle_timeout` because it has no asynchronous stream-writer drain.

## Connection Backpressure

When the server reaches `max_connections`, new connections receive an HTTP `503 Service Unavailable` response with a `Retry-After: 5` header, rather than being silently dropped. This gives clients actionable feedback.

## Streaming Body Limits

`max_request_size` is enforced for buffered and streaming request bodies. Known
oversized bodies are rejected with `413` before app dispatch. HTTP/2 and HTTP/3
oversized DATA paths return deterministic 413/reset behavior. On the HTTP/1
chunked streaming path, the app may see a documented end-of-body signal rather
than a partial body crossing the limit.

## HEAD Request Handling

For `HEAD` requests, the ASGI app may produce response body bytes (to calculate `Content-Length`), but they must not be sent on the wire. Pounce automatically disables compression for HEAD responses to ensure the `Content-Length` header from the app matches what a subsequent `GET` would produce.

## Bodyless Response Protection

HTTP status codes 204 (No Content) and 304 (Not Modified) must not carry a message body per RFC 9110 section 6.4.1. Pounce disables compression for these status codes to prevent compressor flush bytes from producing a body where none is allowed.

## Summary

| Protection | Layer | Config Required |
|---|---|---|
| Proxy header validation | Bridge | `trusted_hosts` |
| CRLF header injection | Bridge | None (always on) |
| Request smuggling prevention | Protocol (h11) | None (always on) |
| Slowloris protection | Worker | `header_timeout` (default: 10s) |
| Connection backpressure | Worker | `max_connections` (default: 10,000) |
| Streaming body limits | Worker | `max_request_size` (default: 1 MB) |
| HEAD compression guard | Bridge | None (always on) |
| Bodyless response guard | Bridge | None (always on) |

## See Also

- [[docs/deployment/production|Production]] — Full production deployment guide
- [[docs/deployment/railway|Railway]] — Edge/origin topology and a proxy-trust worked example
- [[docs/deployment/observability|Observability]] — Health checks and request tracing
- [[docs/configuration/server-config|ServerConfig]] — All configuration options
