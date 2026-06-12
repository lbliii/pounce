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

Pounce provides two complementary load protection mechanisms: **rate limiting** (per-client) and **request queueing** (per-worker load shedding). Use both together for comprehensive protection.

::::{warning}
Both limits are enforced **per worker**, not per server. With `workers > 1` the real ceilings are the configured values multiplied by the worker count. See [Per-Worker Limits](#per-worker-limits-the-aggregate-is-limit--workers) below before sizing them.
::::

| | Rate Limiting | Request Queueing |
|---|---|---|
| Purpose | Prevent per-client abuse | Handle global overload |
| Scope | Per IP address, per worker | Per worker |
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
| `rate_limit_enabled` | `False` | Enable per-IP rate limiting (enforced per worker) |
| `rate_limit_requests_per_second` | `100.0` | Token refill rate, per IP **per worker** |
| `rate_limit_burst` | `200` | Maximum bucket capacity, per IP **per worker** |

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

Bounded queue with load shedding, maintained **per worker** (even in thread mode). When a worker is saturated, its requests queue up to `request_queue_max_depth`. Beyond that, new requests on that worker get an immediate 503.

### Configuration

```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=1000,  # 0 = unlimited (not recommended)
)
```

| Option | Default | Description |
|---|---|---|
| `request_queue_enabled` | `False` | Enable request queueing (per worker, all modes) |
| `request_queue_max_depth` | `1000` | Max queued requests **per worker** (0 = unlimited) |

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

## Per-Worker Limits: the aggregate is `limit x workers`

Both backpressure helpers are wired around your app *before workers spawn*, and
neither shares state across workers. The configured values are therefore
**per worker**, and the server-wide ceiling scales with the worker count.

| Helper | Thread mode (3.14t) | Process mode (GIL build, fork) | Subinterpreter mode |
|---|---|---|---|
| Rate limit | Shared (one limiter) | Per worker (independent copy) | Per worker (independent copy) |
| Request queue | **Per worker** | **Per worker** | **Per worker** |

The request queue is per worker in every mode because it is built on an
`asyncio.Semaphore`, which binds to a single event loop. Rate limiting is shared
only in thread mode, where all workers live in one interpreter; on process and
subinterpreter builds each worker forks/imports its own token-bucket state with
no IPC between them.

### Reasoning about the aggregate

For `N` effective workers (`workers=0` auto-detects to `os.cpu_count()`):

```
effective per-IP rate   = rate_limit_requests_per_second x N   (process/subinterpreter)
effective per-IP burst  = rate_limit_burst              x N    (process/subinterpreter)
effective shed depth    = request_queue_max_depth       x N    (all modes)
```

To hit a target *aggregate*, divide by the worker count yourself. For example,
to cap each IP at a target aggregate rate across 4 process-mode workers, set
`rate_limit_requests_per_second` to that target divided by 4. In thread mode the rate limiter is shared,
so the configured rate already is the aggregate — only the queue depth scales.

::::{note}
Single-worker (`workers=1`) deployments need no adjustment: the configured value
*is* the aggregate. The multiplier only matters once you scale workers.
::::

::::{tip}
These helpers are not a security or auth boundary (a determined client can open
many connections across workers). They protect against accidental overload. For
hard per-IP guarantees, enforce limits at a shared upstream (nginx, HAProxy, an
API gateway) where state is not per worker.
::::

## Performance

- Rate limiting: ~5-10 us/request, ~100 bytes per active IP
- Request queueing: ~1-5 us/request (async semaphore acquire/release)
- Both are designed for free-threading mode
