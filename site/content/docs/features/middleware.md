---
title: Middleware System
description: ASGI3 middleware stack for request/response transformation
weight: 2
---

# Middleware System

Pounce supports server-level middleware for transforming HTTP requests and
responses across your entire application. Middleware runs inside pounce's HTTP
request pipeline, before and after your ASGI app. Lifespan, worker lifecycle,
and WebSocket scopes bypass this middleware stack.

## Configuration

Pass middleware as a list to `ServerConfig`. They execute in order (first = outermost):

```python
from pounce import ServerConfig, CORSMiddleware, SecurityHeadersMiddleware

config = ServerConfig(
    middleware=[
        CORSMiddleware(
            allow_origin="https://example.com",
            allow_methods="GET, POST",
            allow_headers="Authorization",
        ),
        SecurityHeadersMiddleware(),
    ],
)
```

## Built-in Middleware

### CORSMiddleware

Handles Cross-Origin Resource Sharing headers and preflight requests:

```python
from pounce import CORSMiddleware

cors = CORSMiddleware(
    allow_origin="https://example.com",
    allow_methods="GET, POST, PUT, DELETE",
    allow_headers="Authorization, Content-Type",
    max_age=3600,
)
```

### SecurityHeadersMiddleware

Injects security headers on every response:

```python
from pounce import SecurityHeadersMiddleware

security = SecurityHeadersMiddleware()
# Adds: X-Content-Type-Options, X-Frame-Options, etc.
```

## Custom Middleware

Middleware is classified by callable parameter count:

- 1 parameter: pre-request hook, `scope -> scope | Response`
- 2 parameters: exception hook, `(scope, exc) -> Response | None`
- 3 parameters: post-response hook, `(scope, status, headers) -> (status, headers)`

```python
from pounce import Response

# Pre-request: inspect scope, optionally short-circuit
async def auth_middleware(scope):
    token = dict(scope["headers"]).get(b"authorization")
    if not token:
        return Response(status=401, body=b"Unauthorized")
    scope["user"] = "authenticated"
    return scope  # continue to app

# Post-response: modify status or headers
async def timing_middleware(scope, status, headers):
    headers.append((b"x-custom-header", b"value"))
    return status, headers

# Exception: convert selected app errors into responses
async def exception_middleware(scope, exc):
    if isinstance(exc, ValueError):
        return Response(status=400, body=b"Bad Request")
    return None  # re-raise unhandled exceptions
```

## Common Patterns

**Request ID injection** -- pounce injects `X-Request-ID` automatically (see [[docs/deployment/observability|Observability]]).

**Rate limiting** -- use the built-in `rate_limit_enabled` config option instead of middleware (see [[docs/deployment/backpressure|Backpressure]]).

**Logging** -- use `lifecycle_logging=True` for structured request events (see [[docs/features/lifecycle-logging|Lifecycle Logging]]).
