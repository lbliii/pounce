---
title: Rate Limiting
description: Per-IP token bucket rate limiting for abuse protection
draft: false
weight: 90
lang: en
type: doc
tags: [deployment, rate-limiting, security, backpressure]
keywords: [rate limiting, token bucket, per-IP, 429, burst, backpressure]
category: how-to
---

Built-in per-IP rate limiting with token bucket algorithm for production abuse protection.

## Overview

Pounce includes production-grade rate limiting to protect your server from:
- **Abusive clients** - Block excessive requests from single IPs
- **DDoS attacks** - Shed load during traffic spikes
- **API abuse** - Enforce fair usage policies
- **Resource exhaustion** - Prevent server overload

### Token Bucket Algorithm

Classic token bucket rate limiting:
- Tokens refill at a constant rate (requests per second)
- Bucket has maximum capacity (burst size)
- Each request consumes one token
- Requests are denied when bucket is empty
- Each client IP has its own bucket

This allows **burst traffic** while enforcing **sustained rate limits**.

## Quick Start

### Basic Configuration

Enable rate limiting with default settings (100 req/s per IP):

```python
from pounce import ServerConfig

config = ServerConfig(
    rate_limit_enabled=True,
)
```

### Custom Limits

Configure custom rate limits and burst size:

```python
config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=50.0,  # 50 req/s per IP
    rate_limit_burst=100,                  # Allow bursts up to 100
)
```

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rate_limit_enabled` | bool | `False` | Enable per-IP rate limiting |
| `rate_limit_requests_per_second` | float | `100.0` | Sustained rate limit per IP |
| `rate_limit_burst` | int | `200` | Maximum burst capacity per IP |

## How It Works

### Per-IP Tracking

Rate limits are enforced **per client IP address**:
- Each IP gets its own token bucket
- Limits are independent across IPs
- IPv4 and IPv6 are tracked separately

### Token Refill

Tokens refill at a constant rate:
```
refill_rate = rate_limit_requests_per_second
time_between_tokens = 1.0 / refill_rate
```

For 100 req/s:
- New token every 10ms
- 10 tokens per 100ms
- 1000 tokens per 10s

### Burst Handling

Burst capacity allows temporary spikes:
- New clients start with full bucket
- Can immediately consume up to `burst` tokens
- Then limited to sustained rate

**Example:**
- Rate: 10 req/s
- Burst: 50

Client can make:
1. **50 requests instantly** (burst)
2. Then **10 req/s sustained** (rate)

### Memory Management

Automatic cleanup prevents memory leaks:
- Inactive buckets (full capacity) are cleaned up every 5 minutes
- Stale IP tracking is removed automatically
- Memory usage scales with active clients only

## Response Codes

### 429 Too Many Requests

Rate limited requests receive:
```http
HTTP/1.1 429 Too Many Requests
Content-Type: text/plain
Retry-After: 1

Too Many Requests
```

The `Retry-After` header tells clients when to retry (in seconds).

## Examples

### API Server

```python
from pounce import run, ServerConfig

config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=100.0,
    rate_limit_burst=200,
)

run("myapi:app", config=config)
```

### High-Traffic Service

```python
config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=50.0,
    rate_limit_burst=100,
    max_connections=5000,
)
```

### Microservice

```python
config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=1000.0,
    rate_limit_burst=5000,
)
```

## Best Practices

### Choosing Rate Limits

**Conservative (public APIs):**
- Rate: 10-50 req/s per IP
- Burst: 2-5x rate

**Moderate (web apps):**
- Rate: 50-100 req/s per IP
- Burst: 2x rate

**Lenient (internal services):**
- Rate: 100-1000 req/s per IP
- Burst: 5-10x rate

### Monitoring

Track rate limiting effectiveness with Prometheus metrics:
```
http_requests_total{status="429"}  # Rate limited requests
```

### Client Handling

Teach clients to respect rate limits:

**Parse Retry-After:**
```python
import requests

response = requests.get("https://api.example.com/users")
if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 1))
    time.sleep(retry_after)
    # Retry request
```

**Exponential Backoff:**
```python
def make_request_with_backoff(url, max_retries=3):
    for attempt in range(max_retries):
        response = requests.get(url)
        if response.status_code != 429:
            return response

        retry_after = int(response.headers.get("Retry-After", 1))
        backoff = retry_after * (2 ** attempt)
        time.sleep(backoff)

    raise Exception("Rate limited after retries")
```

## Advanced Usage

### Proxy Considerations

When behind a proxy (nginx, HAProxy), rate limiting may see the proxy IP instead of client IP.

**Solution:** Use `trusted_hosts` to extract real client IP:
```python
config = ServerConfig(
    rate_limit_enabled=True,
    trusted_hosts=frozenset({"127.0.0.1", "10.0.0.0/8"}),
)
```

**Security:** Only enable `trusted_hosts` when you control the proxy!

### Per-Route Limits

For different limits per route, use custom middleware:
```python
from pounce._rate_limiter import RateLimiter

# Strict limits for expensive endpoints
strict_limiter = RateLimiter(rate=10.0, burst=20)

# Lenient limits for cheap endpoints
lenient_limiter = RateLimiter(rate=100.0, burst=200)

async def rate_limit_middleware(scope, receive, send):
    if scope["path"].startswith("/api/expensive"):
        if not strict_limiter.check_rate_limit(scope["client"][0]):
            # Return 429
            return
    elif scope["path"].startswith("/api/"):
        if not lenient_limiter.check_rate_limit(scope["client"][0]):
            # Return 429
            return

    # Process request
    await app(scope, receive, send)
```

### Distributed Rate Limiting

Pounce's built-in rate limiting is per-server. For multi-server deployments, consider:

1. **Redis-based rate limiting** - Shared state across servers
2. **Sticky sessions** - Route same IP to same server
3. **Per-server limits** - Each server enforces independently

## Performance Impact

Rate limiting adds minimal overhead:
- **~5-10us per request** - Token bucket check
- **Thread-safe** - Lock-based synchronization
- **Memory efficient** - ~100 bytes per active IP
- **Auto-cleanup** - Stale buckets removed every 5 minutes

For 10,000 active IPs:
- Memory: ~1 MB
- CPU: <0.1% additional load

## Troubleshooting

### False Positives

If legitimate users are rate limited:

1. **Check burst size** - May be too low for bursty traffic
2. **Increase rate** - May be too conservative
3. **Check proxy config** - Multiple users may share proxy IP
4. **Monitor patterns** - Use metrics to identify issues

### No Rate Limiting

If rate limiting isn't working:

1. **Check config** - Ensure `rate_limit_enabled=True`
2. **Verify integration** - Check server logs for "Rate limiting enabled"
3. **Test limits** - Send rapid requests to trigger limit
4. **Check client IP** - Ensure scope["client"] is present

## See Also

- [Request Queueing](./request-queueing) — Global load shedding
- [Observability](./observability) — Monitor rate limiting effectiveness
- [Graceful Shutdown](./graceful-shutdown) — Handle in-flight rate limited requests
