---
title: Backpressure
description: Rate limiting and request queueing for load protection
draft: false
weight: 90
lang: en
type: doc
tags: [deployment, rate-limiting, queueing, backpressure]
keywords: [rate limiting, request queueing, token bucket, load shedding, 429, 503]
category: how-to
---

# Backpressure

Pounce provides two complementary load protection mechanisms: **rate limiting** (per-client) and **request queueing** (global). Use both together for comprehensive protection.

| | Rate Limiting | Request Queueing |
|---|---|---|
| Purpose | Prevent per-client abuse | Handle global overload |
| Scope | Per IP address | All clients |
| Response | 429 Too Many Requests | 503 Service Unavailable |
| Algorithm | Token bucket | Bounded semaphore |

## Rate Limiting

Per-IP token bucket rate limiting. Each client IP gets its own bucket that refills at a steady rate and allows configurable burst.

### Configuration

```python
from pounce import ServerConfig

config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=100.0,  # sustained rate per IP
    rate_limit_burst=200,                   # max burst capacity
)
```

| Option | Default | Description |
|---|---|---|
| `rate_limit_enabled` | `False` | Enable per-IP rate limiting |
| `rate_limit_requests_per_second` | `100.0` | Token refill rate |
| `rate_limit_burst` | `200` | Maximum bucket capacity |

### How It Works

- New clients start with a full bucket
- Each request consumes one token
- Tokens refill at `requests_per_second` rate
- Empty bucket = 429 response with `Retry-After` header
- Inactive buckets are cleaned up every 5 minutes

### Choosing Limits

| Profile | Rate | Burst |
|---|---|---|
| Public API | 10-50 req/s | 2-5x rate |
| Web app | 50-100 req/s | 2x rate |
| Internal service | 100-1000 req/s | 5-10x rate |

### Behind a Proxy

When behind nginx/HAProxy, configure `trusted_hosts` so pounce sees real client IPs:

```python
config = ServerConfig(
    rate_limit_enabled=True,
    trusted_hosts=frozenset({"10.0.0.0/8"}),
)
```

## Request Queueing

Global bounded queue with load shedding. When all workers are busy, requests queue up to a maximum depth. Beyond that, new requests get an immediate 503.

### Configuration

```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=1000,  # 0 = unlimited (not recommended)
)
```

| Option | Default | Description |
|---|---|---|
| `request_queue_enabled` | `False` | Enable request queueing |
| `request_queue_max_depth` | `1000` | Max queued requests (0 = unlimited) |

### Choosing Queue Depth

```
queue_depth = peak_rps * acceptable_wait_seconds
```

- **Conservative** (predictable load): 100-500
- **Moderate** (variable load): 500-1000
- **Aggressive** (bursty traffic): 1000-5000

### Capacity Planning

Monitor 503 rates to inform scaling:

- \> 5% rejection rate = scale up
- 0.1-1% rejection = right-sized
- Queue frequently full = add replicas

## Combined Example

```python
config = ServerConfig(
    # Per-client protection
    rate_limit_enabled=True,
    rate_limit_requests_per_second=100.0,
    rate_limit_burst=200,

    # Global overload protection
    request_queue_enabled=True,
    request_queue_max_depth=500,
)
```

## Client Handling

Both 429 and 503 responses include a `Retry-After` header. Clients should implement exponential backoff:

```python
for attempt in range(max_retries):
    response = requests.get(url)
    if response.status_code not in (429, 503):
        break
    retry_after = int(response.headers.get("Retry-After", 1))
    time.sleep(retry_after * (2 ** attempt))
```

## Performance

- Rate limiting: ~5-10 us/request, ~100 bytes per active IP
- Request queueing: ~1-5 us/request (async semaphore acquire/release)
- Both are designed for free-threading mode
