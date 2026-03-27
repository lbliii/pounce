---
title: Request Queueing
description: Bounded request queues with load shedding for graceful overload handling
draft: false
weight: 95
lang: en
type: doc
tags: [deployment, queueing, load-shedding, backpressure]
keywords: [request queue, load shedding, 503, backpressure, overload, capacity]
category: how-to
---

Application-level request queueing with bounded capacity for graceful overload handling.

## Overview

Request queueing provides graceful degradation under load:
- **Buffer requests** when workers are busy
- **Shed load** when queue fills up (503 responses)
- **Monitor queue depth** and wait times
- **Prevent resource exhaustion** during traffic spikes

### When to Use

Request queueing is ideal for:
- **Bursty traffic** - Handle temporary traffic spikes
- **Background processing** - Queue requests during high load
- **Graceful degradation** - Return 503 instead of timing out
- **Capacity planning** - Monitor queue to identify scaling needs

### Difference from Rate Limiting

| Feature | Rate Limiting | Request Queueing |
|---------|---------------------|------------------------|
| **Purpose** | Prevent per-client abuse | Handle global server overload |
| **Scope** | Per client IP | Global (all clients) |
| **Response** | 429 (rate limited) | 503 (overloaded) |
| **When** | Client exceeds limit | Server at capacity |

Use **both** together for comprehensive protection:
- Rate limiting stops abusive clients
- Request queueing handles legitimate traffic spikes

## Quick Start

### Basic Configuration

Enable request queueing with default settings (queue up to 1000 requests):

```python
from pounce import ServerConfig

config = ServerConfig(
    request_queue_enabled=True,
)
```

### Custom Queue Depth

```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=500,  # Queue up to 500 requests
)
```

### Unlimited Queue

```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=0,  # Unlimited
)
```

**Warning:** Unlimited queues can lead to memory exhaustion under sustained overload!

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `request_queue_enabled` | bool | `False` | Enable request queueing |
| `request_queue_max_depth` | int | `1000` | Maximum queued requests (0 = unlimited) |

## How It Works

### Request Flow

```
1. Request arrives -> Check queue capacity
2. If capacity available -> Acquire queue slot -> Process request -> Release slot
3. If queue full -> Return 503 Service Unavailable
```

### Queue Mechanics

**Bounded Queue:**
- Maximum depth set by `request_queue_max_depth`
- When full, new requests get 503 immediately
- Slots released when requests complete (success or error)

**Unbounded Queue (max_depth=0):**
- No limit on queued requests
- All requests eventually processed
- Risk of memory exhaustion

### Concurrency Control

Uses asyncio Semaphore for slot management:
- Semaphore capacity = `max_depth`
- `acquire()` - Try to get queue slot (non-blocking)
- `release()` - Return slot when done
- Thread-safe for concurrent requests

## Response Codes

### 503 Service Unavailable

Requests rejected when queue is full receive:
```http
HTTP/1.1 503 Service Unavailable
Content-Type: text/plain
Retry-After: 5

Service Unavailable - Server Overloaded
```

The `Retry-After` header tells clients to wait 5 seconds before retrying.

## Examples

### Web Application

```python
from pounce import run, ServerConfig

config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=1000,
    max_connections=100,
    workers=4,
)

run("webapp:app", config=config)
```

### API Server

Combine with rate limiting:
```python
config = ServerConfig(
    # Rate limiting per client
    rate_limit_enabled=True,
    rate_limit_requests_per_second=100.0,
    rate_limit_burst=200,

    # Global request queueing
    request_queue_enabled=True,
    request_queue_max_depth=500,
)
```

### Background Worker

Conservative queue for long-running tasks:
```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=100,
    max_connections=10,
    request_timeout=300.0,
)
```

## Best Practices

### Choosing Queue Depth

**Conservative (predictable load):**
- Queue depth: 100-500
- Reject requests quickly when overloaded

**Moderate (variable load):**
- Queue depth: 500-1000 (default)
- Buffer moderate traffic spikes

**Aggressive (bursty traffic):**
- Queue depth: 1000-5000
- Handle large traffic bursts

**Formula:**
```
queue_depth = peak_rps * acceptable_wait_seconds
```

### Monitoring

Track queue health with Prometheus metrics:
```
http_requests_total{status="503"}  # Queue rejections
```

### Client Handling

**Parse Retry-After:**
```python
import requests
import time

response = requests.get("https://api.example.com/users")
if response.status_code == 503:
    retry_after = int(response.headers.get("Retry-After", 5))
    time.sleep(retry_after)
```

**Exponential Backoff:**
```python
def make_request_with_backoff(url, max_retries=5):
    for attempt in range(max_retries):
        response = requests.get(url)

        if response.status_code != 503:
            return response

        retry_after = int(response.headers.get("Retry-After", 5))
        backoff = retry_after * (2 ** attempt)
        time.sleep(min(backoff, 60))

    raise Exception("Server overloaded after retries")
```

### Capacity Planning

Use queue metrics for scaling decisions:

**Under-provisioned (scale up):**
- High rejection rate (>5%)
- High average wait time (>500ms)
- Queue frequently full

**Right-sized:**
- Rejection rate: 0.1-1%
- Average wait: 50-200ms
- Queue absorbs spikes

## Advanced Usage

### Per-Route Queues

Different queue depths per route:

```python
from pounce._request_queue import RequestQueue, create_queue_wrapper

expensive_queue = RequestQueue(max_depth=100)
cheap_queue = RequestQueue(max_depth=1000)

async def route_aware_queueing(scope, receive, send):
    if scope["path"].startswith("/api/expensive"):
        wrapper = create_queue_wrapper(app, expensive_queue)
    else:
        wrapper = create_queue_wrapper(app, cheap_queue)

    await wrapper(scope, receive, send)
```

## Performance Impact

Request queueing adds minimal overhead:
- **~1-5us per request** - Queue acquire/release
- **Thread-safe** - asyncio Semaphore
- **Memory efficient** - ~50 bytes per queued request
- **No blocking** - Async acquire (try-lock pattern)

## Troubleshooting

### High Rejection Rate

If many requests get 503:

1. **Increase queue depth** - Allow more buffering
2. **Scale workers** - Add more processing capacity
3. **Optimize app** - Reduce request processing time
4. **Add replicas** - Horizontal scaling

### No Load Shedding

If 503s aren't being sent:

1. **Check config** - Ensure `request_queue_enabled=True`
2. **Verify integration** - Check logs for "Request queueing enabled"
3. **Test capacity** - Send more requests than `max_connections`

## See Also

- [Rate Limiting](./rate-limiting) — Per-client abuse prevention
- [Observability](./observability) — Monitor queue metrics
- [Graceful Shutdown](./graceful-shutdown) — Handle queued requests during shutdown
