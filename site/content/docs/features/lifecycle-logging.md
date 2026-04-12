---
title: Lifecycle Logging
description: Structured connection and request lifecycle events with correlation IDs
weight: 5
---

# Lifecycle Logging

Pounce captures structured events throughout the connection lifecycle for production debugging. Beyond access logs, lifecycle logging tracks connection establishment, client disconnects, slow requests, and connection teardown with correlation IDs.

## Quick Start

```python
from pounce import ServerConfig

config = ServerConfig(
    lifecycle_logging=True,
    log_format="json",
    log_slow_requests_threshold=2.0,  # seconds
)
```

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `lifecycle_logging` | `False` | Enable structured lifecycle events |
| `log_slow_requests_threshold` | `5.0` | Seconds before a request is flagged slow |
| `log_format` | `"auto"` | `"json"` for machine parsing, `"text"` for development |
| `health_check_path` | `None` | Exclude health checks from lifecycle logs |

Events are emitted to the `pounce.lifecycle` logger.

## Event Types

### ConnectionOpened (DEBUG)

New TCP connection accepted.

Fields: `connection_id`, `worker_id`, `client_addr`, `client_port`, `protocol` (`h1`/`h2`/`websocket`), `timestamp`

### RequestStarted (DEBUG)

HTTP request head parsed.

Fields: `connection_id`, `worker_id`, `method`, `path`, `http_version`, `timestamp`

### ResponseCompleted (INFO if slow, DEBUG otherwise)

HTTP response fully sent.

Fields: `connection_id`, `worker_id`, `status`, `bytes_sent`, `duration_ms`, `slow` (boolean, present only when true), `timestamp`

### ClientDisconnected (WARNING)

Client closed connection unexpectedly.

Fields: `connection_id`, `worker_id`, `during_streaming` (boolean), `timestamp`

### ConnectionCompleted (DEBUG)

TCP connection closed. `reason` is one of: `complete`, `timeout`, `client_disconnect`, `error`, `backpressure`.

Fields: `connection_id`, `worker_id`, `requests_served`, `total_bytes_sent`, `duration_ms`, `reason`, `timestamp`

## Example Output

```json
{"event": "ConnectionOpened", "connection_id": 1, "worker_id": 1, "client_addr": "127.0.0.1", "protocol": "h1", "timestamp": "2026-02-12T10:15:30.123Z"}
{"event": "RequestStarted", "connection_id": 1, "worker_id": 1, "method": "GET", "path": "/api/users", "timestamp": "2026-02-12T10:15:30.125Z"}
{"event": "ResponseCompleted", "connection_id": 1, "worker_id": 1, "status": 200, "bytes_sent": 1024, "duration_ms": 3500.0, "slow": true, "timestamp": "2026-02-12T10:15:33.625Z"}
{"event": "ConnectionCompleted", "connection_id": 1, "worker_id": 1, "requests_served": 1, "duration_ms": 3502.5, "reason": "complete", "timestamp": "2026-02-12T10:15:33.627Z"}
```

## Querying Logs

```bash
# Find slow requests
cat app.log | jq 'select(.slow == true)'

# Trace a connection
cat app.log | jq 'select(.connection_id == 42)'

# Count requests per worker
cat app.log | jq -r 'select(.event == "ResponseCompleted") | .worker_id' | sort | uniq -c

# Find frequent disconnects
cat app.log | jq -r 'select(.event == "ClientDisconnected") | .client_addr' | sort | uniq -c | sort -rn
```

## Performance

~40 us overhead per request (4 events x ~10 us each). Set `log_level="info"` to skip DEBUG events and reduce volume by ~75%.

## Comparison with Access Logs

| | Access Logs | Lifecycle Logs |
|---|---|---|
| Scope | Request/response summary | Full connection lifecycle |
| Events | 1 per request | 4+ per request |
| Detail | Status, bytes, duration | Protocol, disconnects, correlation |
| Use case | HTTP metrics | Production debugging |

Use both together: access logs for dashboards, lifecycle logs for debugging.
