# Pounce Features Overview

Complete feature set for pounce — the free-threading-native ASGI server for Python 3.14t.

## Core Protocol Support

### HTTP/1.1
- ✅ Pure Python parser (h11)
- ✅ Fast built-in parser (~3 µs/req, sync worker hot path)
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
- ✅ **Compression** (permessage-deflate, RFC 7692)
- ✅ Per-message deflate negotiation
- ✅ Configurable compression parameters

**Docs:** [WebSocket Compression](./features/websocket-compression.md)

### HTTP/3
- ✅ QUIC/UDP transport via bengal-zoomies (pure Python)
- ✅ Separate datagram protocol worker
- ✅ Requires TLS (ssl_certfile + ssl_keyfile)
- ✅ Configurable idle timeout (`http3_idle_timeout`)
- 🔄 0-RTT connection resumption (planned)
- 🔄 Connection migration (planned)

**Docs:** [HTTP/3 Roadmap](./design/http3-roadmap.md)

## Server Features

### Zero-Downtime Rolling Reload

Production-grade, Kubernetes-level rolling reload that ensures zero dropped requests during code deployments. This is a first-class feature designed for environments where downtime is unacceptable.

**How it works:**

1. A reload signal is received (USR1 or programmatic call).
2. The supervisor spawns a **new generation** of worker threads with the updated code.
3. New workers begin accepting connections immediately.
4. Old-generation workers enter **drain mode** -- they stop accepting new connections but continue processing in-flight requests to completion.
5. Once all in-flight requests finish (or the drain timeout expires), old workers are terminated.
6. The reload is complete with zero dropped requests.

```
 Signal received          New workers ready         Old workers drained
      |                        |                          |
      v                        v                          v
 +---------+    +---------+---------+    +---------+
 | Old Gen |    | Old Gen | New Gen |    | New Gen |
 | serving | -> | drain   | accept  | -> | serving |
 +---------+    +---------+---------+    +---------+
```

**Important:** Rolling reload only works in **3.14t thread mode**, where workers are threads in a shared interpreter. In GIL-enabled process mode, Pounce falls back to a full restart (all workers stopped, then restarted).

**Usage:**
```bash
# Send USR1 to the supervisor process
kill -USR1 <pid>

# Or programmatically
await supervisor.reload()
```

### AcceptDistributor: Thundering Herd Elimination

On macOS and Windows, `SO_REUSEPORT` is either unavailable or does not provide kernel-level load balancing across sockets. This means that when multiple workers share a listening socket, a new incoming connection wakes **all** blocked `accept()` calls -- the classic "thundering herd" problem. Only one worker wins; the rest waste CPU cycles waking up for nothing.

Pounce solves this with the **AcceptDistributor**: a single dedicated thread calls `accept()` on the listening socket and distributes connections round-robin into per-worker bounded queues. Each worker pulls from its own queue with zero contention against other workers.

**Activation:** The AcceptDistributor activates automatically when all three conditions are met:
- Multi-worker configuration (`workers > 1`)
- Thread mode (free-threading / 3.14t)
- Shared listening socket (no `SO_REUSEPORT`)

**Why this matters:** Most Python ASGI servers (Uvicorn, Hypercorn) either ignore the thundering herd problem or rely on `SO_REUSEPORT` which is Linux-only for fair distribution. Pounce's AcceptDistributor provides fair, efficient connection distribution on every platform, making it uniquely suited for macOS development and Windows deployment scenarios.

### 1. Static File Serving
- ✅ Chunked file serving with configurable buffer size
- ✅ ETag generation and validation (If-None-Match)
- ✅ Range requests (byte-range serving for video/large files)
- ✅ Pre-compressed file serving (`.gz`, `.zst`)
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

**Performance:** Files are served in 65 KB chunks with async streaming to avoid blocking the event loop.

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
- ✅ Rich structured logging for debugging and monitoring
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

### 11. Rate Limiting
- ✅ Per-IP token bucket rate limiting
- ✅ Configurable requests/second and burst size
- ✅ Returns 429 Too Many Requests when exceeded
- ✅ Thread-safe for free-threading

**Configuration:**
```python
ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=100,
    rate_limit_burst=200,
)
```

**Docs:** [Rate Limiting](./deployment/rate-limiting.md)

### 12. Request Queueing
- ✅ Bounded request queue for overload protection
- ✅ Returns 503 Service Unavailable when queue is full
- ✅ Configurable queue depth

**Configuration:**
```python
ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=1000,
)
```

**Docs:** [Request Queueing](./deployment/request-queueing.md)

### 13. Sentry Integration
- ✅ Error tracking and exception capture
- ✅ Performance monitoring and profiling
- ✅ Request context capture
- ✅ Graceful degradation if sentry-sdk not installed

**Configuration:**
```python
ServerConfig(
    sentry_dsn="https://...",
    sentry_environment="staging",
    sentry_traces_sample_rate=0.1,
)
```

**Docs:** [Sentry Integration](./deployment/sentry.md)

### 14. Proxy Header Support
- ✅ Trusted reverse proxy support
- ✅ X-Forwarded-For/Proto/Host handling
- ✅ RFC 7239 `Forwarded` header support
- ✅ Spoofing prevention via trusted host list

**Configuration:**
```python
ServerConfig(
    trusted_hosts=["10.0.0.0/8"],
)
```

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

### Safety Defaults
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

### I/O Operations
- ✅ Chunked file serving (65 KB async reads)
- ✅ Direct socket writes (no buffering)
- ✅ Streaming response bodies

### Connection Pooling
- ✅ HTTP/1.1 keep-alive
- ✅ HTTP/2 stream multiplexing
- ✅ Configurable max requests per connection

### Efficient Parsing
- ✅ Built-in fast H1 parser (~3 µs/req, sync worker hot path)
- ✅ h11 pure Python parser (async worker)
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
| **HTTP/3** | ✅ Yes | ❌ No | ✅ Yes | ❌ No |
| **Static files** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **Middleware** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **OpenTelemetry** | ✅ Native | ⚠️ Via lib | ⚠️ Via lib | ❌ No |
| **Lifecycle logging** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **Graceful shutdown** | ✅ K8s-ready | ⚠️ Basic | ⚠️ Basic | ⚠️ Basic |
| **Dev error pages** | ✅ Rich | ❌ No | ❌ No | ❌ No |
| **Pure Python** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ Rust core |
| **zstd compression** | ✅ Stdlib | ❌ No | ❌ No | ❌ No |
| **Rate limiting** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **Request queueing** | ✅ Built-in | ❌ No | ❌ No | ❌ No |
| **Sentry** | ✅ Native | ❌ No | ❌ No | ❌ No |

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

### Implemented
- ✅ HTTP/1.1, HTTP/2, HTTP/3, WebSocket (all four protocols)
- ✅ Free-threading worker model with GIL fallback
- ✅ Static files, middleware, compression, rate limiting, request queueing
- ✅ OpenTelemetry, Prometheus, Sentry, lifecycle logging
- ✅ Graceful shutdown, graceful reload, health checks

### Planned
- 🔄 HTTP/3 0-RTT connection resumption
- 🔄 HTTP/3 connection migration
- 🔄 `concurrent.interpreters` as third worker model (PEP 734)

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
- ✅ **46 tests skipped** for optional dependencies (h2, wsproto)

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

## Competitive Advantages

Summary of key differentiators across the Python ASGI server landscape:

| Dimension | Pounce | Uvicorn | Hypercorn | Granian |
|-----------|--------|---------|-----------|---------|
| **Free-threading support** | Native -- designed for 3.14t from day one | Compatibility mode only | No support | Partial (Rust core limits benefit) |
| **HTTP/1.1 parser speed** | ~3 us/req built-in fast parser + h11 fallback | h11 only (~15 us/req) | h11 only | Rust parser (fast, but C-ext) |
| **Config thread-safety** | Frozen dataclass, zero-copy across threads | Mutable config, not thread-safe | Mutable config | N/A (Rust-managed) |
| **Rolling reload** | Zero-downtime, generational worker swap (thread mode) | Full restart only | Full restart only | Full restart only |
| **Thundering herd fix** | AcceptDistributor with per-worker queues | No mitigation | No mitigation | No mitigation |
| **Built-in metrics** | Lifecycle events, Prometheus, OpenTelemetry native | No built-in metrics | No built-in metrics | No built-in metrics |
| **Pure Python** | Yes -- no C extensions, no GIL re-enablement | Yes | Yes | No (Rust core) |

**Key takeaways:**

- **Pounce is the only Python ASGI server built natively for free-threading.** Other servers treat 3.14t as a compatibility target; Pounce treats it as the primary runtime.
- **Rolling reload and thundering herd elimination** are production-critical features that no other pure-Python ASGI server provides.
- **Pure Python with no C extensions** means Pounce never accidentally re-enables the GIL on 3.14t builds, preserving true parallelism across all worker threads.
- **Built-in observability** (lifecycle events, Prometheus, OpenTelemetry, Sentry) eliminates the need for external instrumentation libraries that may not be free-threading safe.

---

**Built for Python 3.14t free-threading.**
