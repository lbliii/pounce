# OpenTelemetry Integration

Pounce provides **native OpenTelemetry support** for distributed tracing, enabling you to monitor requests across your entire infrastructure with platforms like Jaeger, Datadog, Tempo, and others.

## Overview

When enabled, pounce automatically:

- **Creates spans** for every HTTP request
- **Propagates context** via W3C Trace Context headers
- **Records attributes** (method, path, status, duration)
- **Exports traces** to your observability platform via OTLP

**Zero code changes required.** Just configure an endpoint.

## Quick Start

### Install OpenTelemetry

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

### Configure Pounce

```python
from pounce import ServerConfig

config = ServerConfig(
    otel_endpoint="http://localhost:4318",  # Your OTLP collector
    otel_service_name="my-api",            # Service name in traces
)
```

Or via environment variables:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=my-api

pounce myapp:app
```

### Start Your Collector

Using Jaeger:

```bash
docker run -d --name jaeger \
  -p 4318:4318 \
  -p 16686:16686 \
  jaegertracing/all-in-one:latest
```

View traces at `http://localhost:16686`

## Configuration

### Endpoint

The OTLP HTTP endpoint where traces are exported:

```python
ServerConfig(otel_endpoint="http://localhost:4318")
```

Pounce automatically appends `/v1/traces` if not present.

**Common endpoints:**

| Platform | Endpoint |
|----------|----------|
| Local Jaeger | `http://localhost:4318` |
| Local OTEL Collector | `http://localhost:4318` |
| Datadog Agent | `http://localhost:4318` |
| Honeycomb | `https://api.honeycomb.io` |
| Tempo | `http://tempo:4318` |

### Service Name

Identifies your service in traces:

```python
ServerConfig(otel_service_name="auth-service")
```

This appears as the service name in your observability platform.

### Disabling OpenTelemetry

OTel is disabled by default. Only enabled when `otel_endpoint` is set:

```python
# Disabled (default)
config = ServerConfig()

# Enabled
config = ServerConfig(otel_endpoint="http://localhost:4318")
```

## What Gets Traced

### Automatic Span Creation

Every HTTP request creates a span with:

**Span name:** `{METHOD} {path}`
Example: `GET /api/users/42`

**Attributes (HTTP Semantic Conventions):**
- `http.method`: Request method (GET, POST, etc.)
- `http.target`: Request path
- `http.scheme`: URL scheme (http/https)
- `http.host`: Server hostname
- `http.status_code`: Response status code
- `http.response_content_length`: Response size in bytes
- `net.host.port`: Server port

**Status:**
- `OK` for 2xx-4xx responses
- `ERROR` for 5xx responses and exceptions

### Context Propagation

Pounce automatically extracts and injects W3C Trace Context headers:

**Incoming request:**
```
traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
```

Pounce parses this and **continues the trace**, making your request part of a larger distributed trace.

**Outgoing (downstream) requests:**

If your app makes HTTP calls, inject the context:

```python
from pounce._otel import inject_trace_context

# In your ASGI app
headers = [(b"content-type", b"application/json")]
headers = inject_trace_context(headers)  # Adds traceparent

# Make downstream request with propagated context
await httpx.get("https://api.example.com", headers=dict(headers))
```

### Exception Recording

Unhandled exceptions are automatically recorded on spans:

```python
async def app(scope, receive, send):
    raise ValueError("Database connection failed")
    # ↑ This exception appears in your trace with full details
```

The span status is set to `ERROR` and includes the exception message.

## Observability Platforms

### Jaeger

```bash
docker run -d --name jaeger \
  -p 4318:4318 \
  -p 16686:16686 \
  jaegertracing/all-in-one:latest
```

```python
config = ServerConfig(
    otel_endpoint="http://localhost:4318",
    otel_service_name="my-service",
)
```

View: `http://localhost:16686`

### Datadog

```python
config = ServerConfig(
    otel_endpoint="http://localhost:4318",  # Datadog Agent OTLP endpoint
    otel_service_name="my-service",
)
```

Ensure the Datadog Agent has OTLP enabled:

```yaml
# datadog.yaml
otlp_config:
  receiver:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
```

### Grafana Tempo

```python
config = ServerConfig(
    otel_endpoint="http://tempo:4318",
    otel_service_name="my-service",
)
```

View traces in Grafana.

### Honeycomb

```python
import os

config = ServerConfig(
    otel_endpoint="https://api.honeycomb.io",
    otel_service_name="my-service",
)

os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"x-honeycomb-team={API_KEY}"
```

### Custom OTEL Collector

```python
config = ServerConfig(
    otel_endpoint="http://otel-collector:4318",
    otel_service_name="my-service",
)
```

The collector can route to multiple backends (Jaeger, Tempo, Datadog, etc.).

## Advanced Usage

### Custom Span Attributes

Add custom attributes to the current span:

```python
from pounce._otel import add_span_attribute

async def app(scope, receive, send):
    # Add custom business logic attributes
    add_span_attribute("user.id", "12345")
    add_span_attribute("tenant.name", "acme-corp")
    add_span_attribute("feature.flag", "new-ui")

    # Process request...
```

### Manual Span Creation

For finer control, use the span manager directly:

```python
from pounce._otel import RequestSpanManager

manager = RequestSpanManager(service_name="worker-service")

with manager.create_request_span(
    method="PROCESS",
    path="/background/task",
    headers=[],
) as span:
    # Your background task
    process_data()

    manager.record_response(span, status_code=200)
```

### Distributed Tracing Example

**Service A (API Gateway):**

```python
# pounce automatically propagates trace context
async def app(scope, receive, send):
    # Call Service B - context auto-propagated if using httpx with OTel
    response = await httpx.get("http://service-b/process")
    ...
```

**Service B (Worker):**

```python
# Receives trace context, continues the same trace
async def app(scope, receive, send):
    # This span appears as a child of Service A's span
    result = await process_data()
    ...
```

Both services' spans appear in the **same trace** in your observability platform.

## Performance

### Overhead

OpenTelemetry adds minimal overhead:

- **Span creation:** ~50μs per request
- **Export:** Batched asynchronously (no request blocking)
- **Memory:** ~1KB per active span

For most applications, this is **< 1% overhead**.

### Sampling

To reduce volume in high-traffic applications, configure sampling:

```python
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

# Sample 10% of traces
sampler = TraceIdRatioBased(0.1)

# Apply before pounce.run()
```

(Pounce uses the global OTel configuration, so standard OTel sampling works.)

### Batch Export

Spans are exported in batches (default: every 5 seconds or 512 spans) to minimize network overhead.

## Troubleshooting

### "OpenTelemetry endpoint configured but package not installed"

**Problem:** Endpoint set but OTel not installed

**Solution:**

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

### Traces Not Appearing

**Problem:** No traces in Jaeger/Datadog/Tempo

**Checklist:**

1. Verify collector is running: `curl http://localhost:4318/v1/traces`
2. Check pounce logs for export errors
3. Verify `otel_endpoint` is correct
4. Ensure firewall allows outbound OTLP traffic
5. Check collector/backend ingestion logs

### High Cardinality Warnings

**Problem:** Too many unique span names

**Cause:** Using request IDs or user IDs in span names

**Solution:** Use attributes instead:

```python
# ❌ Bad: High cardinality span names
span_name = f"GET /users/{user_id}"

# ✅ Good: Use attributes
span_name = "GET /users/:id"
add_span_attribute("user.id", user_id)
```

### Context Not Propagating

**Problem:** Downstream services not linked in traces

**Cause:** HTTP library not propagating context

**Solution:** Use OpenTelemetry instrumentation:

```bash
pip install opentelemetry-instrumentation-httpx
```

```python
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

HTTPXClientInstrumentor().instrument()

# Now httpx automatically propagates context
await httpx.get("http://service-b/api")
```

## Security Considerations

1. **Endpoint Security**: Use HTTPS for OTLP endpoints in production
2. **PII in Spans**: Avoid putting sensitive data in span attributes
3. **Sampling**: Enable sampling to control data volume and costs
4. **Network Access**: Ensure OTLP endpoint is only accessible to authorized services

## Comparison with Other Servers

| Server | OpenTelemetry Support | Auto-instrumentation |
|--------|----------------------|---------------------|
| **pounce** | ✅ Built-in | ✅ Automatic spans |
| Uvicorn | ⚠️ Via library | ❌ Manual only |
| Gunicorn | ❌ No | ❌ No |
| Hypercorn | ⚠️ Via library | ❌ Manual only |

Pounce is the **only** ASGI server with native, zero-config OpenTelemetry integration.

## Best Practices

1. **Consistent Naming**: Use clear, consistent service names across your infrastructure
2. **Attributes Over Names**: Put variable data in attributes, not span names
3. **Sampling**: Enable sampling in high-traffic prod environments
4. **Instrumentation**: Auto-instrument HTTP clients for full distributed traces
5. **Monitor Exports**: Set up alerts for failed trace exports

## See Also

- [Structured Logging](../features/structured-logging.md) — Correlate logs with traces
- [Production Deployment](./production.md) — Production observability setup
- [Server Timing](../features/server-timing.md) — Browser performance traces
