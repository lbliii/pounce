# Structured Lifecycle Event Logging

Pounce provides **rich structured logging** of connection lifecycle events for production debugging and observability. Beyond standard access logs, lifecycle logging captures detailed events like connection establishment, client disconnects, and slow requests with full correlation IDs.

## Overview

Lifecycle logging captures events throughout the request/response lifecycle:

- **connection.opened** — New TCP connection accepted (protocol negotiation)
- **request.started** — HTTP request head parsed
- **response.completed** — HTTP response fully sent (with duration)
- **client.disconnected** — Client closed connection unexpectedly
- **connection.closed** — TCP connection closed (with stats)
- **request.slow** — Request exceeded duration threshold

All events include **correlation IDs** (connection_id, worker_id) for distributed tracing and debugging.

## Quick Start

### Enable Lifecycle Logging

```python
from pounce import ServerConfig

config = ServerConfig(
    lifecycle_logging=True,  # Enable structured lifecycle events
    log_format="json",       # JSON for machine parsing
    log_slow_requests_threshold=2.0,  # Log requests > 2 seconds
)
```

### Example Output (JSON)

```json
{"event": "ConnectionOpened", "connection_id": 1, "worker_id": 1, "client_addr": "127.0.0.1", "protocol": "h1", "timestamp": "2026-02-12T10:15:30.123456Z"}
{"event": "RequestStarted", "connection_id": 1, "worker_id": 1, "method": "GET", "path": "/api/users", "http_version": "1.1", "timestamp": "2026-02-12T10:15:30.125000Z"}
{"event": "ResponseCompleted", "connection_id": 1, "worker_id": 1, "status": 200, "bytes_sent": 1024, "duration_ms": 3500.0, "slow": true, "timestamp": "2026-02-12T10:15:33.625000Z"}
{"event": "ConnectionClosed", "connection_id": 1, "worker_id": 1, "requests_served": 1, "total_bytes_sent": 1024, "duration_ms": 3502.5, "reason": "complete", "timestamp": "2026-02-12T10:15:33.627500Z"}
```

## Configuration

### lifecycle_logging

Enable structured lifecycle event logging.

```python
config = ServerConfig(
    lifecycle_logging=True,  # Disabled by default
)
```

When enabled, lifecycle events are logged to the `pounce.lifecycle` logger.

**Default:** `False`

### log_slow_requests_threshold

Threshold (in seconds) for logging slow requests at INFO level.

```python
config = ServerConfig(
    log_slow_requests_threshold=2.0,  # Log requests > 2 seconds
)
```

Requests faster than this threshold are logged at DEBUG level. Slow requests are logged at INFO with `"slow": true` flag.

**Default:** `5.0` seconds

### log_format

Controls output format for all logs (access logs + lifecycle events).

```python
config = ServerConfig(
    log_format="json",  # "text" or "json"
)
```

**JSON format** (recommended for production):
```json
{"event": "ResponseCompleted", "connection_id": 42, "status": 200, "duration_ms": 150.5, ...}
```

**Text format** (human-readable for development):
```
Response completed: {'event': 'ResponseCompleted', 'connection_id': 42, ...}
```

**Default:** `"text"`

### health_check_path

Filter health check requests from lifecycle logs to reduce noise.

```python
config = ServerConfig(
    lifecycle_logging=True,
    health_check_path="/health",  # Don't log /health requests
)
```

Health check requests to this path won't generate lifecycle events, preventing log spam from Kubernetes liveness probes.

## Event Types

### ConnectionOpened

Emitted when a new TCP connection is accepted.

**Fields:**
- `event`: "ConnectionOpened"
- `connection_id`: Unique connection identifier
- `worker_id`: Worker that accepted the connection
- `client_addr`: Client IP address
- `client_port`: Client port
- `server_addr`: Server bind address
- `server_port`: Server port
- `protocol`: "h1", "h2", or "websocket"
- `timestamp`: ISO 8601 timestamp

**Log level:** DEBUG

**Example:**
```json
{
  "event": "ConnectionOpened",
  "connection_id": 1,
  "worker_id": 1,
  "client_addr": "192.168.1.100",
  "client_port": 54321,
  "server_addr": "0.0.0.0",
  "server_port": 8000,
  "protocol": "h1",
  "timestamp": "2026-02-12T10:15:30.123456Z"
}
```

### RequestStarted

Emitted when an HTTP request head is fully parsed.

**Fields:**
- `event`: "RequestStarted"
- `connection_id`: Correlation ID
- `worker_id`: Worker handling the request
- `method`: HTTP method (GET, POST, etc.)
- `path`: Request path
- `http_version`: HTTP version (1.0, 1.1, 2.0)
- `timestamp`: ISO 8601 timestamp

**Log level:** DEBUG

**Example:**
```json
{
  "event": "RequestStarted",
  "connection_id": 1,
  "worker_id": 1,
  "method": "POST",
  "path": "/api/orders",
  "http_version": "1.1",
  "timestamp": "2026-02-12T10:15:30.125000Z"
}
```

### ResponseCompleted

Emitted when an HTTP response is fully sent.

**Fields:**
- `event`: "ResponseCompleted"
- `connection_id`: Correlation ID
- `worker_id`: Worker that handled the request
- `status`: HTTP status code
- `bytes_sent`: Response body size in bytes
- `duration_ms`: Request duration in milliseconds
- `slow`: `true` if duration exceeded threshold (only present on slow requests)
- `timestamp`: ISO 8601 timestamp

**Log level:** INFO (slow requests), DEBUG (fast requests)

**Example (slow request):**
```json
{
  "event": "ResponseCompleted",
  "connection_id": 1,
  "worker_id": 1,
  "status": 200,
  "bytes_sent": 10240,
  "duration_ms": 3500.0,
  "slow": true,
  "timestamp": "2026-02-12T10:15:33.625000Z"
}
```

### ClientDisconnected

Emitted when the client closes the connection unexpectedly.

**Fields:**
- `event`: "ClientDisconnected"
- `connection_id`: Correlation ID
- `worker_id`: Worker handling the connection
- `during_streaming`: `true` if disconnect occurred during response streaming
- `timestamp`: ISO 8601 timestamp

**Log level:** WARNING

**Example:**
```json
{
  "event": "ClientDisconnected",
  "connection_id": 1,
  "worker_id": 1,
  "during_streaming": true,
  "timestamp": "2026-02-12T10:15:31.500000Z"
}
```

### ConnectionClosed

Emitted when a TCP connection is closed (by either side).

**Fields:**
- `event`: "ConnectionClosed"
- `connection_id`: Correlation ID
- `worker_id`: Worker that owned the connection
- `requests_served`: Number of requests processed on this connection
- `total_bytes_sent`: Total bytes sent across all requests
- `duration_ms`: Total connection duration in milliseconds
- `reason`: Reason for closure:
  - `"complete"` — Normal close after response
  - `"timeout"` — Keep-alive timeout
  - `"client_disconnect"` — Client closed early
  - `"error"` — Protocol error
  - `"backpressure"` — Max connections limit
- `timestamp`: ISO 8601 timestamp

**Log level:** DEBUG

**Example:**
```json
{
  "event": "ConnectionClosed",
  "connection_id": 1,
  "worker_id": 1,
  "requests_served": 5,
  "total_bytes_sent": 20480,
  "duration_ms": 5000.5,
  "reason": "timeout",
  "timestamp": "2026-02-12T10:15:35.625500Z"
}
```

## Use Cases

### 1. Debug Slow Requests

Find requests taking longer than expected:

```bash
# Filter slow requests from logs
cat app.log | jq 'select(.slow == true)'
```

Example output:
```json
{
  "event": "ResponseCompleted",
  "connection_id": 42,
  "worker_id": 2,
  "status": 200,
  "duration_ms": 7500.0,
  "slow": true,
  "timestamp": "2026-02-12T10:15:33.625000Z"
}
```

**Action:** Investigate the endpoint, database queries, or external API calls.

### 2. Trace Request Through Lifecycle

Use `connection_id` to trace a request from start to finish:

```bash
# Get all events for connection_id 42
cat app.log | jq 'select(.connection_id == 42)'
```

Example output:
```json
{"event": "ConnectionOpened", "connection_id": 42, ...}
{"event": "RequestStarted", "connection_id": 42, "path": "/api/users", ...}
{"event": "ResponseCompleted", "connection_id": 42, "status": 200, "duration_ms": 150.5, ...}
{"event": "ConnectionClosed", "connection_id": 42, "requests_served": 1, ...}
```

### 3. Identify Connection Issues

Find clients that disconnect frequently:

```bash
# Count client disconnects by client_addr
cat app.log | jq -r 'select(.event == "ClientDisconnected") | .client_addr' | sort | uniq -c | sort -rn
```

### 4. Monitor Worker Load

See which workers are handling the most requests:

```bash
# Count requests by worker_id
cat app.log | jq -r 'select(.event == "ResponseCompleted") | .worker_id' | sort | uniq -c
```

Example output:
```
  150 1
  145 2
  148 3
  157 4
```

Helps identify load balancing issues or stuck workers.

### 5. Analyze Connection Reuse

Check how many requests are served per connection (HTTP/1.1 keep-alive efficiency):

```bash
# Get requests_served distribution
cat app.log | jq -r 'select(.event == "ConnectionClosed") | .requests_served' | sort -n | uniq -c
```

Example:
```
  500 1  # 500 connections served only 1 request (no reuse)
  200 2  # 200 connections served 2 requests
  100 3  # Keep-alive working!
```

## Integration with Observability Platforms

### Elasticsearch / Kibana

Ship JSON logs to Elasticsearch for powerful queries:

```bash
# Log to file
pounce myapp:app --log-format json > /var/log/pounce/app.log

# Ship with Filebeat
filebeat -e -c filebeat.yml
```

**filebeat.yml:**
```yaml
filebeat.inputs:
  - type: log
    paths:
      - /var/log/pounce/*.log
    json.keys_under_root: true
    json.add_error_key: true

output.elasticsearch:
  hosts: ["localhost:9200"]
  index: "pounce-%{+yyyy.MM.dd}"
```

**Kibana query:**
```
event: "ResponseCompleted" AND slow: true
```

### Datadog

Use the Datadog Agent to collect JSON logs:

```yaml
# /etc/datadog-agent/conf.d/pounce.d/conf.yaml
logs:
  - type: file
    path: /var/log/pounce/*.log
    service: pounce-api
    source: pounce
    sourcecategory: http_web_access
```

Filter slow requests in Datadog:
```
@event:ResponseCompleted @slow:true
```

### Grafana Loki

Stream logs to Loki with Promtail:

```yaml
# promtail.yaml
clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: pounce
    static_configs:
      - targets:
          - localhost
        labels:
          job: pounce
          __path__: /var/log/pounce/*.log
    pipeline_stages:
      - json:
          expressions:
            event: event
            connection_id: connection_id
            slow: slow
```

**Loki query:**
```
{job="pounce"} | json | slow = "true"
```

## Performance

### Overhead

Lifecycle logging overhead is **minimal**:

- **Event creation:** ~200ns per event (frozen dataclass)
- **JSON serialization:** ~10μs per event
- **Log writing:** Async, non-blocking

For a typical request (ConnectionOpened → RequestStarted → ResponseCompleted → ConnectionClosed):
- **4 events** × 10μs = **40μs total overhead**
- **< 0.1% overhead** for most applications

### Log Level Filtering

Set `log_level` to reduce overhead:

```python
# Production: Only log slow requests (INFO level)
config = ServerConfig(
    lifecycle_logging=True,
    log_level="info",  # Filters out DEBUG events
)
```

With `log_level="info"`:
- Only `ResponseCompleted` (slow), `ClientDisconnected`, and errors are logged
- Fast requests (DEBUG) are skipped → **~75% fewer events**

## Best Practices

### 1. Use JSON Format in Production

```python
config = ServerConfig(
    log_format="json",  # Machine-parseable, structured
    lifecycle_logging=True,
)
```

JSON is easier to parse, query, and aggregate in log management systems.

### 2. Ship Logs to Centralized Storage

Don't rely on local log files — ship to:
- **Elasticsearch** (with Filebeat)
- **Datadog** (with Datadog Agent)
- **Loki** (with Promtail)
- **CloudWatch** (with CloudWatch Agent)
- **Splunk** (with Splunk Forwarder)

### 3. Set Appropriate Slow Request Threshold

```python
# API server
config = ServerConfig(log_slow_requests_threshold=1.0)  # 1 second

# Database-heavy app
config = ServerConfig(log_slow_requests_threshold=5.0)  # 5 seconds

# Real-time API
config = ServerConfig(log_slow_requests_threshold=0.5)  # 500ms
```

Set based on your app's performance expectations.

### 4. Filter Health Checks

```python
config = ServerConfig(
    lifecycle_logging=True,
    health_check_path="/health",  # Kubernetes probes
)
```

Prevents health check spam (can be hundreds of requests per minute).

### 5. Correlate with OpenTelemetry

Combine lifecycle logging with distributed tracing:

```python
config = ServerConfig(
    lifecycle_logging=True,
    otel_endpoint="http://localhost:4318",  # Jaeger/Tempo
)
```

Use `connection_id` from logs to find the corresponding trace in Jaeger.

## Comparison with Access Logs

| Feature | Access Logs | Lifecycle Logs |
|---------|-------------|----------------|
| **Scope** | Request/response summary | Full connection lifecycle |
| **Events** | 1 per request | 4+ per request |
| **Detail** | Status, bytes, duration | Protocol, disconnects, correlation |
| **Overhead** | Minimal | Low |
| **Use Case** | HTTP metrics | Production debugging |

**Use both together** for comprehensive observability:
- **Access logs** for HTTP metrics and dashboards
- **Lifecycle logs** for debugging production issues

## Troubleshooting

### Logs Not Appearing

**Problem:** Lifecycle events not showing in logs

**Checklist:**

1. ✅ `lifecycle_logging=True` in config
2. ✅ `log_level` is DEBUG or INFO (not WARNING/ERROR)
3. ✅ Check `pounce.lifecycle` logger is configured

### Too Many Logs

**Problem:** Log volume too high

**Solutions:**

1. **Raise log level:**
   ```python
   config = ServerConfig(log_level="info")  # Only slow requests
   ```

2. **Increase slow threshold:**
   ```python
   config = ServerConfig(log_slow_requests_threshold=10.0)  # 10s
   ```

3. **Filter health checks:**
   ```python
   config = ServerConfig(health_check_path="/health")
   ```

### JSON Parsing Errors

**Problem:** Invalid JSON in logs

**Cause:** Likely mixing text and JSON formats

**Solution:** Ensure consistent format:
```python
config = ServerConfig(log_format="json")  # All logs as JSON
```

## See Also

- [Access Logs](../deployment/access-logs.md) — HTTP request/response logging
- [OpenTelemetry Integration](../deployment/opentelemetry.md) — Distributed tracing
- [Production Deployment](../deployment/production.md) — Log shipping and aggregation
- [Observability](../deployment/observability.md) — Complete observability stack
