---
title: Features
description: Complete feature set for pounce ASGI server
weight: 30
---

# Features

Pounce provides a comprehensive feature set for modern web applications, with particular focus on production deployment, observability, and developer experience.

## Core Protocol Support

- **HTTP/1.1** — Pure Python (h11) + fast built-in parser (~3 µs/req)
- **HTTP/2** — Stream multiplexing, header compression, priority signals
- **WebSocket** — Full RFC 6455 support with compression
- **HTTP/3** — QUIC/UDP via bengal-zoomies (requires TLS)

[Learn more about protocols →](/docs/protocols/)

## Production Features

### Static File Serving
Chunked file serving with ETags, range requests, and pre-compressed file support.

[Read more →](/docs/features/static-files/)

### Middleware System
ASGI3 middleware stack for request/response transformation.

[Read more →](/docs/features/middleware/)

### WebSocket Compression
permessage-deflate compression (RFC 7692) with automatic negotiation.

[Read more →](/docs/protocols/websocket/)

### Graceful Reload
Zero-downtime code updates with rolling worker restart.

[Read more →](/docs/deployment/workers/)

### Development Error Pages
Rich HTML error pages with syntax highlighting and security-safe production mode.

### OpenTelemetry Integration
Native distributed tracing with automatic span creation and W3C Trace Context.

[Read more →](/docs/deployment/observability/)

### Connection Draining
Production-grade graceful shutdown with Kubernetes support.

[Read more →](/docs/deployment/production/)

### Lifecycle Logging
Structured event logging for production debugging with correlation IDs.

[Read more →](/docs/deployment/observability/)

## Observability

- **Structured logging** — JSON or text format
- **OpenTelemetry** — Native OTLP exporter
- **Lifecycle events** — Connection and request tracking
- **Server-Timing headers** — Built-in performance metrics
- **Prometheus metrics** — Built-in collector

[Learn more about observability →](/docs/deployment/observability/)

## Development Experience

- **Auto-reload** — Watch files and restart on changes
- **Rich error pages** — Syntax-highlighted tracebacks
- **CLI interface** — Simple command-line usage
- **Type-safe configuration** — Validated at startup

[Get started →](/docs/get-started/)

## Deployment Features

- **Worker management** — Threads (3.14t) or processes (GIL)
- **Graceful shutdown** — Kubernetes-ready
- **TLS/SSL** — TLS 1.3 support
- **Compression** — zstd, gzip
- **Health checks** — Configurable health check endpoint (opt-in via `health_check_path`)

[Deployment guides →](/docs/deployment/)
