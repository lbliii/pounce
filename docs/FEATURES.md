# Pounce Features Overview

Complete feature set for pounce — the free-threading-native ASGI server for Python 3.14t.

## Core Protocol Support

### HTTP/1.1
- ✅ Pure Python parser (h11) — zero dependencies
- ✅ C-accelerated parser (httptools) — optional, 2-3x faster
- ✅ Keep-alive connection pooling
- ✅ Chunked transfer encoding
- ✅ Request/response streaming
- ✅ Pipeline support (multiple requests per connection)

**Docs:** Protocol parsing, connection management

### HTTP/2
- ✅ Stream multiplexing (h2 library)
- ✅ Server push (optional)
- ✅ Priority signals (RFC 7540)
- ✅ Flow control and backpressure
- ✅ ALPN negotiation (via TLS)
- ✅ Upgrade from HTTP/1.1 (h2c)

**Docs:** [HTTP/2 features](../features/), ALPN configuration

### WebSocket
- ✅ RFC 6455 compliant (wsproto)
- ✅ WebSocket over HTTP/1.1
- ✅ WebSocket over HTTP/2 (RFC 8441)
- ✅ **Compression** (permessage-deflate) — Phase 5b
- ✅ Per-message deflate negotiation
- ✅ Configurable compression parameters

**Docs:** [WebSocket Compression](./features/websocket-compression.md)

### HTTP/3 (Planned)
- 🔄 **Phase 5c** (Q2 2026) — QUIC/UDP transport
- 🔄 bengal-zoomies for pure-Python QUIC
- 🔄 0-RTT connection resumption
- 🔄 Alt-Svc discovery

**Docs:** [HTTP/3 Roadmap](./design/http3-roadmap.md)

## Phase 5b Features (Production-Ready)

### 1. Static File Serving
- ✅ Zero-copy sendfile() on supported platforms
- ✅ ETag generation and validation (If-None-Match)
- ✅ Range requests (byte-range serving for video/large files)
- ✅ Pre-compressed file serving (`.gz`, `.br`, `.zst`)
- ✅ Automatic Content-Encoding negotiation
- ✅ Configurable cache-control headers
- ✅ Directory index files (index.html)
- ✅ MIME type detection
- ✅ Symlink following control

**Configuration:**
```python
ServerConfig(
    static_files={"/static": "./public"},
    static_precompressed=True,
    static_cache_control="public, max-age=3600",
)
```

**Performance:** Zero-copy sendfile() provides 2-3x faster file serving than buffered reads.

### 2. Middleware Extension System
- ✅ ASGI3 middleware support
- ✅ Middleware wrapping at server level
- ✅ Request/response transformation
- ✅ Authentication, logging, compression middleware
- ✅ Chainable middleware stack

**Configuration:**
```python
ServerConfig(
    middleware=[
        AuthMiddleware,
        LoggingMiddleware,
        CompressionMiddleware,
    ]
)
```

**Use cases:** CORS, authentication, request ID injection, custom headers

### 3. ASGI Lifespan State Sharing
- ✅ ASGI 3.0 spec-compliant lifespan state
- ✅ Share state between lifespan startup and request handlers
- ✅ Database connection pools, HTTP clients, caches
- ✅ Proper cleanup on shutdown

**Example:**
```python
async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        # Store DB pool in state
        scope["state"]["db"] = await create_pool()
    else:
        # Access DB pool from request
        db = scope["state"]["db"]
```

**Benefits:** Efficient resource management, proper lifecycle handling

### 4. Development Error Pages
- ✅ Rich HTML error pages with syntax highlighting
- ✅ Full exception traceback with local variables
- ✅ Source code context (5 lines before/after)
- ✅ Request details (method, path, headers)
- ✅ **Security:** Sensitive data redaction (passwords, tokens, secrets)
- ✅ Production mode: simple 500 error (no leaks)

**Configuration:**
```python
ServerConfig(
    debug=True,  # Enable rich error pages (dev only!)
)
```

**Docs:** [Development Error Pages](./development/error-pages.md)

### 5. Graceful Worker Reload
- ✅ Zero-downtime code updates
- ✅ Rolling restart (one worker at a time)
- ✅ Connection draining during reload
- ✅ Configurable reload timeout
- ✅ File watcher integration (optional)

**Usage:**
```bash
# Send SIGHUP for graceful reload
kill -HUP $SUPERVISOR_PID

# Or use CLI flag for auto-reload
pounce myapp:app --reload
```

**Docs:** [Graceful Reload](./deployment/graceful-reload.md)

### 6. OpenTelemetry Integration
- ✅ Native OTLP HTTP exporter (no instrumentation needed)
- ✅ Automatic span creation for every request
- ✅ W3C Trace Context propagation (traceparent/tracestate)
- ✅ HTTP semantic conventions (method, path, status, duration)
- ✅ Exception recording on spans
- ✅ Integration with Jaeger, Datadog, Tempo, Honeycomb

**Configuration:**
```python
ServerConfig(
    otel_endpoint="http://localhost:4318",
    otel_service_name="my-api",
)
```

**Docs:** [OpenTelemetry Integration](./deployment/opentelemetry.md)

### 7. Enhanced Connection Draining
- ✅ Graceful SIGTERM handling
- ✅ Reject new connections during shutdown
- ✅ Wait for active connections to complete
- ✅ Force-terminate after timeout
- ✅ **Kubernetes-ready** (terminationGracePeriodSeconds support)
- ✅ Detailed drain logging

**Configuration:**
```python
ServerConfig(
    shutdown_timeout=30.0,  # Wait up to 30s for connections to drain
)
```

**Kubernetes:**
```yaml
terminationGracePeriodSeconds: 40  # > shutdown_timeout + buffer
```

**Docs:** [Graceful Shutdown](./deployment/graceful-shutdown.md)

### 8. Structured Lifecycle Event Logging
- ✅ Rich structured logging for production debugging
- ✅ JSON or text format output
- ✅ Correlation IDs (connection_id, worker_id)
- ✅ Slow request detection and logging
- ✅ Connection lifecycle tracking
- ✅ Health check filtering

**Events logged:**
- `ConnectionOpened` — New connection with protocol negotiation
- `RequestStarted` — HTTP request head parsed
- `ResponseCompleted` — Response sent (with duration)
- `ClientDisconnected` — Unexpected client disconnect
- `ConnectionCompleted` — Connection completed with stats

**Configuration:**
```python
ServerConfig(
    lifecycle_logging=True,
    log_format="json",
    log_slow_requests_threshold=2.0,  # 2 seconds
    health_check_path="/health",
)
```

**Docs:** [Lifecycle Logging](./features/lifecycle-logging.md)

### 9. Health Check Endpoint
- ✅ Built-in `/health` endpoint (configurable path)
- ✅ Zero-overhead health checks
- ✅ Kubernetes liveness/readiness probe support
- ✅ Filtered from access logs and lifecycle logs

**Configuration:**
```python
ServerConfig(
    health_check_path="/health",
)
```

**Kubernetes probe:**
```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  periodSeconds: 10
```

### 10. WebSocket Compression
- ✅ permessage-deflate compression (RFC 7692)
- ✅ Automatic negotiation via Sec-WebSocket-Extensions
- ✅ Configurable compression parameters
- ✅ Per-message compression control
- ✅ Memory-efficient streaming compression

**Configuration:**
```python
ServerConfig(
    websocket_compression=True,
    websocket_max_message_size=10_485_760,  # 10 MB
)
```

**Docs:** [WebSocket Compression](./features/websocket-compression.md)

## Built-in Observability

### Access Logging
- ✅ Structured access logs (text or JSON)
- ✅ Customizable log format
- ✅ Request timing and byte counts
- ✅ Client IP and user agent
- ✅ Optional access log filtering

### Server-Timing Headers
- ✅ Automatic `Server-Timing` header injection
- ✅ Parse/app/encode durations
- ✅ Browser DevTools integration
- ✅ Zero overhead when disabled

**Configuration:**
```python
ServerConfig(
    server_timing=True,
)
```

### Lifecycle Events
- ✅ Frozen dataclass events (immutable)
- ✅ Nanosecond timestamps (monotonic)
- ✅ Connection/request/response tracking
- ✅ Custom collectors (buffered, metrics, logging)
- ✅ Zero overhead with NoopCollector

### Prometheus Metrics (Built-in)
- ✅ PrometheusCollector for lifecycle events
- ✅ Request count, duration, bytes sent
- ✅ Connection count, errors
- ✅ Worker stats

## Worker Management

### Adaptive Worker Model
- ✅ **Free-threading (3.14t):** N threads, shared memory
- ✅ **GIL builds:** N processes, isolated memory
- ✅ Automatic detection via `sys._is_gil_enabled()`
- ✅ SO_REUSEPORT socket sharing
- ✅ Worker supervision and restart
- ✅ Graceful shutdown coordination

### Configuration
```python
ServerConfig(
    workers=0,  # Auto-detect from os.cpu_count()
    max_connections=10_000,
    backlog=2048,
)
```

### Worker Restart Budget
- ✅ Automatic worker restart on crashes
- ✅ Restart budget (max 5 restarts per 60s)
- ✅ Prevents restart storms
- ✅ Supervisor coordination

## Content Encoding

### Compression
- ✅ **zstd** — Stdlib compression.zstd (PEP 784), zero dependencies
- ✅ **gzip** — Stdlib zlib
- ❌ **brotli** — Excluded (C extension re-enables GIL on 3.14t)
- ✅ Automatic Accept-Encoding negotiation
- ✅ Minimum size threshold
- ✅ Pre-compressed file serving

**Configuration:**
```python
ServerConfig(
    compression=True,
    compression_min_size=500,  # bytes
)
```

### Transfer Encoding
- ✅ Chunked transfer encoding (streaming)
- ✅ Content-Length (buffered)
- ✅ Mixed mode support

## Security Features

### TLS/SSL
- ✅ TLS 1.3 support (mandatory for HTTP/3)
- ✅ Certificate loading (certfile/keyfile)
- ✅ ALPN negotiation (h2, http/1.1, h3)
- ✅ SNI support
- ✅ Truststore integration

**Configuration:**
```python
ServerConfig(
    ssl_certfile="cert.pem",
    ssl_keyfile="key.pem",
)
```

### Production Safety
- ✅ Sensitive data redaction (error pages)
- ✅ Debug mode disabled by default
- ✅ Header size limits
- ✅ Request size limits
- ✅ Connection limits
- ✅ Request timeout enforcement

### Headers
- ✅ Server header customization
- ✅ Date header generation
- ✅ Security headers (configurable via middleware)
- ✅ CORS support (via middleware)

## Configuration Management

### Immutable Config
- ✅ Frozen dataclass (thread-safe)
- ✅ Validated at construction
- ✅ Shared across all workers (zero-copy in threads)
- ✅ Type-safe with full type hints

### Configuration Sources
- ✅ Programmatic (Python code)
- ✅ CLI arguments
- ✅ Environment variables
- ✅ Configuration file (optional)

### Validation
- ✅ Comprehensive validation in `__post_init__`
- ✅ Clear error messages
- ✅ Range checks for all numeric values
- ✅ Mutual exclusion checks (e.g., ssl_certfile/ssl_keyfile)

## Development Experience

### Auto-Reload
- ✅ Watch source files for changes
- ✅ Graceful restart on file modification
- ✅ Configurable file extensions
- ✅ Directory exclusions

**Configuration:**
```python
ServerConfig(
    reload=True,
    reload_include=(".html", ".css"),
    reload_dirs=("templates", "static"),
)
```

### Rich Error Pages
- ✅ Syntax-highlighted tracebacks (Rosettes integration)
- ✅ Local variable inspection
- ✅ Source code context
- ✅ Request details
- ✅ Security-safe (redacts sensitive data)

### Logging
- ✅ Structured logging (text or JSON)
- ✅ Configurable log levels
- ✅ Per-logger configuration (pounce, chirp, lifecycle)
- ✅ Access log filtering

## Performance Features

### Zero-Copy Operations
- ✅ sendfile() for static files (2-3x faster)
- ✅ Direct socket writes (no buffering)
- ✅ Streaming response bodies

### Connection Pooling
- ✅ HTTP/1.1 keep-alive
- ✅ HTTP/2 stream multiplexing
- ✅ Configurable max requests per connection

### Efficient Parsing
- ✅ Built-in fast H1 parser (~3 µs/req, sync worker hot path)
- ✅ h11 pure Python parser (async worker)
- ✅ httptools C-accelerated parser (optional, 2-3x faster)
- ✅ h2 for HTTP/2 HPACK compression

### Backpressure
- ✅ TCP backpressure propagation
- ✅ ASGI backpressure via receive/send
- ✅ Connection limits

## Limits and Timeouts

### Request Limits
```python
ServerConfig(
    max_request_size=1_048_576,  # 1 MB
    max_header_size=65_536,      # 64 KB
    max_headers=100,
)
```

### Connection Limits
```python
ServerConfig(
    max_connections=10_000,
    max_requests_per_connection=0,  # unlimited
)
```

### Timeouts
```python
ServerConfig(
    keep_alive_timeout=5.0,
    request_timeout=30.0,
    header_timeout=10.0,
    shutdown_timeout=10.0,
    reload_timeout=30.0,
)
```

## Platform Support

### Operating Systems
- ✅ Linux (primary platform)
- ✅ macOS (development)
- ✅ Windows (limited testing)

### Python Versions
- ✅ Python 3.14+ (free-threading and GIL builds)
- ✅ Python 3.13 (GIL only, via fallback)

### Container Platforms
- ✅ Docker
- ✅ Kubernetes
- ✅ AWS ECS/Fargate
- ✅ Google Cloud Run
- ✅ Azure Container Instances

## Comparison with Other Servers

| Feature | pounce | Uvicorn | Hypercorn | Granian |
|---------|--------|---------|-----------|---------|
| **Free-threading native** | ✅ Yes | ⚠️ Compat | ❌ No | ⚠️ Partial |
| **HTTP/2** | ✅ Yes | ❌ No | ✅ Yes | ✅ Yes |
| **WebSocket** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **WS Compression** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **HTTP/3** | 🔄 Phase 5c | ❌ No | ✅ Yes | ❌ No |
| **Static files** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **Middleware** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **OpenTelemetry** | ✅ Native | ⚠️ Via lib | ⚠️ Via lib | ❌ No |
| **Lifecycle logging** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **Graceful shutdown** | ✅ K8s-ready | ⚠️ Basic | ⚠️ Basic | ⚠️ Basic |
| **Dev error pages** | ✅ Rich | ❌ No | ❌ No | ❌ No |
| **Pure Python** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ Rust core |
| **zstd compression** | ✅ Stdlib | ❌ No | ❌ No | ❌ No |

## Ecosystem Integration

### Bengal Static Site Generator
- ✅ Native integration (design target)
- ✅ Optimized for SSG serving patterns
- ✅ Pre-compressed static files

### Chirp Web Framework
- ✅ Built specifically for Chirp (but framework-agnostic)
- ✅ Server-Timing integration
- ✅ Streaming HTML support

### Rosettes Syntax Highlighter
- ✅ Used for dev error pages
- ✅ Python syntax highlighting
- ✅ Pure Python (no Pygments dependency)

### ASGI Compatibility
- ✅ ASGI 3.0 specification compliant
- ✅ Works with FastAPI, Starlette, Quart, Django, Flask (via asgiref)
- ✅ Lifespan protocol support

## Roadmap

### Phase 5b (Current — COMPLETE)
- ✅ All 10 features implemented and tested

### Phase 5c (Q2 2026)
- 🔄 HTTP/3 (QUIC) support
- 🔄 Advanced stream prioritization
- 🔄 Connection migration

### Phase 5d (Future)
- 🔄 Custom QUIC congestion control (BBR)
- 🔄 Advanced observability dashboards
- 🔄 Performance optimizations

## Documentation

| Category | Description |
|----------|-------------|
| [Features](./features/) | Feature-specific documentation |
| [Deployment](./deployment/) | Production deployment guides |
| [Development](./development/) | Development tools and debugging |
| [Design](./design/) | Architectural design documents |

## Testing

- ✅ **900+ tests** covering all features
- ✅ **Integration tests** for multi-worker scenarios
- ✅ **ASGI compliance tests** for spec conformance
- ✅ **46 tests skipped** for optional dependencies (httptools, h2, wsproto)

**Run tests:**
```bash
pytest tests/unit/        # Unit tests
pytest tests/integration/ # Integration tests
pytest                    # All tests
```

## License

MIT License — see [LICENSE](../LICENSE) for details.

## Contributing

pounce is part of the Bengal ecosystem. See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.

---

**Built with ❤️ for Python 3.14t free-threading.**
