# Pounce Examples

Example code and prototypes for pounce features. Each example is a standalone
ASGI app you can run directly.

## Running examples

Most examples expose an ASGI `app` (or a `create_app()` factory) and are meant
to be served with the canonical CLI:

```bash
pounce serve --app examples.<name>:app
```

A few examples (`production_server`, `metrics_monitoring`, `rate_limiting_demo`,
`static_files`, `http3_prototype`, `programmatic_server`, `subinterpreter_server`)
configure the server in code and are launched with `python examples/<name>.py`.
The run command in the index below is authoritative for each example.

Optional extras some examples need:

```bash
pip install bengal-pounce[ws]    # WebSocket examples (wsproto)
pip install bengal-pounce[h3]    # HTTP/3 prototype (zoomies)
pip install bengal-chirp         # chirp framework example
```

## Index

All 19 examples (excluding `__init__.py`) are listed below. The smoke tests in
[`tests/integration/test_examples.py`](../tests/integration/test_examples.py)
keep these run commands and endpoints honest.

### Getting Started

| File | What it shows | Run | Endpoints / output | Extras | Status |
|------|---------------|-----|--------------------|--------|--------|
| `hello.py` | The simplest ASGI app — start here. | `pounce serve --app examples.hello:app` | `GET /` → `Hello, World!` | none | production |
| `factory_app.py` | App-factory pattern (`module:create_app()`). | `pounce serve --app examples.factory_app:create_app()` | `GET /` → `Hello from factory!` | none | production |
| `programmatic_server.py` | Start/serve/shutdown the `pounce.Server` from code. | `python examples/programmatic_server.py` | `GET /` → greeting; self-shuts down after 3s | none | production |
| `lifespan.py` | ASGI lifespan startup/shutdown with a thread-safe counter. | `pounce serve --app examples.lifespan:app` | `GET /` → `request #N` | none | production |
| `lifespan_state.py` | Stores interpreter-safe values in lifespan `state`. | `pounce serve --app examples.lifespan_state:app` | `GET /` → `app_name` from state | none | production |

### HTTP Patterns

| File | What it shows | Run | Endpoints / output | Extras | Status |
|------|---------------|-----|--------------------|--------|--------|
| `mini_router.py` | Routing + middleware as plain function composition on raw ASGI. | `pounce serve --app examples.mini_router:app` | `GET /` (routes JSON), `GET /users/{id}`, `POST /echo`, 404 otherwise | none | example |
| `compression_demo.py` | Automatic zstd/gzip negotiation on a ~2 KB payload. | `pounce serve --app examples.compression_demo:app` | `GET /` → JSON; observe `Content-Encoding` | none | production |
| `static_files.py` | `StaticFiles` mounts with custom cache control and MIME types. | `python examples/static_files.py` | `GET /` (API), `/static/`, `/assets/` | none | production |
| `file_upload.py` | Streaming uploads with visible backpressure / flow control. | `pounce serve --app examples.file_upload:app --server-timing` | `GET /` (upload form), `POST /upload` → byte stats JSON | none | production |
| `streaming_sse.py` | Server-Sent Events streamed chunk-by-chunk (never buffered). | `pounce serve --app examples.streaming_sse:app` | `GET /` → `text/event-stream` (`heartbeat` + `message` events) | none | production |

### Realtime

| File | What it shows | Run | Endpoints / output | Extras | Status |
|------|---------------|-----|--------------------|--------|--------|
| `websocket_echo.py` | WebSocket handshake + echo; 426 for plain HTTP. | `pounce serve --app examples.websocket_echo:app` | `WebSocket /ws` echoes messages; `GET /` → 426 | `ws` | production |
| `websocket_chat.py` | Multi-client broadcast chat over shared, lock-protected state. | `pounce serve --app examples.websocket_chat:app --workers 4` | `GET /` (chat HTML), `WebSocket /ws` broadcast | `ws` | example |

### Runtime

| File | What it shows | Run | Endpoints / output | Extras | Status |
|------|---------------|-----|--------------------|--------|--------|
| `cpu_parallel.py` | CPU-bound handler showing free-threading parallelism. | `pounce serve --app examples.cpu_parallel:app --workers 4 --no-access-log` | `GET /` → JSON with `digest` + `iterations` | none | example |
| `subinterpreter_server.py` | Subinterpreter worker mode (PEP 734). | `pounce serve --app examples.subinterpreter_server:app --workers 4 --worker-mode subinterpreter` | `GET /` → per-interpreter request count | none (Python 3.14+) | example |

### Observability & Production

| File | What it shows | Run | Endpoints / output | Extras | Status |
|------|---------------|-----|--------------------|--------|--------|
| `production_server.py` | Full production config: metrics, rate limiting, queueing, hot reload, optional Sentry. | `python examples/production_server.py` | `GET /`, `/health`, `/metrics`, `/slow`, `/error` | `sentry-sdk` (optional, via `SENTRY_DSN`) | production |
| `rate_limiting_demo.py` | Token-bucket per-IP rate limiting (5 req/s, burst 10) with a dashboard. | `python examples/rate_limiting_demo.py` | `GET /` (dashboard), `/api`, `/metrics`; 429 when limited | none | example |
| `metrics_monitoring.py` | Live Prometheus metrics + dashboard. | `python examples/metrics_monitoring.py` | `GET /` (dashboard), `/metrics`, `/api/fast`, `/api/slow`, `/api/error` | none | example |

### Prototypes

| File | What it shows | Run | Endpoints / output | Extras | Status |
|------|---------------|-----|--------------------|--------|--------|
| `chirp_app.py` | Serving a chirp (companion framework) app unmodified. | `pounce serve --app examples.chirp_app:app` | `GET /` → chirp response | `bengal-chirp` | prototype |
| `http3_prototype.py` | Minimal HTTP/3 (QUIC) request/response via zoomies. | `python examples/http3_prototype.py` | `GET /` over HTTP/3 on `:4433` (TLS required) | `h3` + TLS cert | prototype |

> **Status legend:** `production` — safe pattern to copy into real apps;
> `example` — illustrative, tuned for demos (low limits, localhost binds);
> `prototype` — optional-limited, not part of the core contract.

## Production Server

**File:** `production_server.py`

Complete production configuration demonstrating all Phase 6 features:
- Prometheus metrics endpoint
- Per-IP rate limiting
- Request queueing and load shedding
- Sentry error tracking (optional — enabled when `SENTRY_DSN` is set)
- Hot reload for zero-downtime deployments

```bash
python examples/production_server.py
```

**Features demonstrated:**
- `/` - API endpoint with JSON response
- `/metrics` - Prometheus metrics in text format
- `/health` - Built-in health check
- `/slow` - Slow endpoint for queue testing
- `/error` - Error endpoint (reported to Sentry when configured)

**Configuration highlights:**
```python
config = ServerConfig(
    metrics_enabled=True,
    rate_limit_enabled=True,
    request_queue_enabled=True,
    sentry_dsn=os.getenv("SENTRY_DSN"),  # optional; inert when unset
    workers=4,  # For zero-downtime reload
)
```

> **Security:** this example binds `0.0.0.0` and serves `/metrics` with no
> auth. Firewall `/metrics` or place it behind auth before public exposure.

## Rate Limiting Demo

**File:** `rate_limiting_demo.py`

Interactive demonstration of token bucket rate limiting:
- Per-IP rate limits (5 req/s)
- Burst capacity (10 requests)
- Visual web dashboard
- 429 responses when rate limited

```bash
python examples/rate_limiting_demo.py
```

Visit `http://localhost:8000/` for interactive dashboard.

**Test from command line:**
```bash
# Send 5 requests (should succeed - within burst)
for i in {1..5}; do curl http://localhost:8000/api; done

# Send 20 requests (will hit rate limit)
for i in {1..20}; do curl http://localhost:8000/api; sleep 0.05; done

# View metrics
curl http://localhost:8000/metrics | grep 429
```

## Metrics Monitoring

**File:** `metrics_monitoring.py`

Live metrics dashboard showing:
- Total requests
- Active connections
- P95 latency
- Error rate

```bash
python examples/metrics_monitoring.py
```

Visit `http://localhost:8000/` for live dashboard.

**Prometheus integration:**
Add to your `prometheus.yml`:
```yaml
scrape_configs:
  - job_name: 'pounce-demo'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 5s
```

**Test endpoints:**
- `/api/fast` - Fast response (~10ms)
- `/api/slow` - Slow response (~500ms)
- `/api/error` - Returns 500 error

## Protocol Examples

## HTTP/3

**Status:** Optional-limited prototype. Requires TLS and the `h3` extra.

**File:** `http3_prototype.py`

This example runs a minimal HTTP/3 server using pounce's [zoomies](https://github.com/lbliii/zoomies) integration. HTTP/3 remains optional-limited: request/response handling is available, but lifecycle parity, reload/drain proof, shutdown behavior, 0-RTT policy, and benchmark proof are still tracked in the protocol proof ledger.

### Requirements

```bash
pip install bengal-pounce[h3]
```

### Generate TLS Certificate

HTTP/3 (QUIC) requires TLS 1.3. Generate a self-signed certificate for testing:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes
```

### Run the Prototype

```bash
python examples/http3_prototype.py
```

### Test with Chrome

```bash
google-chrome --enable-quic --origin-to-force-quic-on=localhost:4433 https://localhost:4433
```

Or with curl (if built with HTTP/3 support):

```bash
curl --http3 https://localhost:4433
```

### What It Demonstrates

- UDP socket binding for QUIC
- HTTP/3 request/response handling
- ASGI scope creation for HTTP/3
- Integration with zoomies (sans-I/O QUIC/HTTP/3)

### Current Caveats

- HTTP/3 is not part of the core contract.
- Lifecycle, reload/drain, shutdown, and benchmark parity are not yet complete.
- WebSocket over HTTP/3 is not supported.

### Architecture

```
┌─────────────────────┐
│  ASGI Application   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ ZoomiesDatagram     │  ← HTTP/3 request/response
│ Protocol (zoomies)  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   QuicConnection    │  ← QUIC transport (zoomies)
│   (RFC 9000)        │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│    UDP Socket       │  ← Network I/O
└─────────────────────┘
```

### See Also

- [Protocol Proof Ledger](../docs/design/protocol-proof-ledger.json) — Current HTTP/3 status and gaps
- [HTTP/3 Roadmap](../docs/design/http3-roadmap.md) — Historical design context
- [zoomies](https://github.com/lbliii/zoomies) — Free-threading-native QUIC/HTTP/3 library
- [RFC 9114: HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html) — Protocol specification
