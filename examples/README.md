# Pounce Examples

Example code and prototypes for pounce features.

## Production Examples

### Production Server

**File:** `production_server.py`

Complete production configuration demonstrating all Phase 6 features:
- Prometheus metrics endpoint
- Per-IP rate limiting
- Request queueing and load shedding
- Sentry error tracking (optional)
- Hot reload for zero-downtime deployments

```bash
python examples/production_server.py
```

**Features demonstrated:**
- `/` - API endpoint with JSON response
- `/metrics` - Prometheus metrics in text format
- `/health` - Built-in health check
- `/slow` - Slow endpoint for queue testing
- `/error` - Error endpoint for Sentry testing

**Configuration highlights:**
```python
config = ServerConfig(
    metrics_enabled=True,
    rate_limit_enabled=True,
    request_queue_enabled=True,
    sentry_dsn=os.getenv("SENTRY_DSN"),
    workers=4,  # For zero-downtime reload
)
```

### Rate Limiting Demo

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

### Metrics Monitoring

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

## Prototypes

## HTTP/3 Prototype

**Status:** Conceptual prototype for Phase 5c (Q2 2026)

**File:** `http3_prototype.py`

This prototype demonstrates how HTTP/3/QUIC support would be integrated into pounce using the [aioquic](https://github.com/aiortc/aioquic) library.

### Requirements

```bash
pip install aioquic
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
- ALPN negotiation for HTTP/3 (`h3`)
- HTTP/3 request/response handling
- ASGI scope creation for HTTP/3
- Integration with aioquic library

### Limitations

This is a **conceptual prototype**, not a production-ready implementation:

- No worker supervision
- No graceful shutdown
- No connection pooling
- No performance optimization
- No comprehensive error handling

The full implementation in Phase 5c will integrate HTTP/3 into the existing worker architecture with proper supervision, lifecycle management, and production-grade features.

### Architecture

```
┌─────────────────────┐
│  ASGI Application   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  H3ServerProtocol   │  ← HTTP/3 request/response
│  (aioquic wrapper)  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   QuicConnection    │  ← QUIC transport (aioquic)
│   (RFC 9000)        │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│    UDP Socket       │  ← Network I/O
└─────────────────────┘
```

### See Also

- [HTTP/3 Roadmap](../docs/design/http3-roadmap.md) — Full architectural design and implementation plan
- [aioquic documentation](https://aioquic.readthedocs.io/) — Library reference
- [RFC 9114: HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html) — Protocol specification
