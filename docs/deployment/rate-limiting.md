# Rate Limiting

**Phase 6.2 Complete: Rate Limiting & Backpressure** ✅

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

### Normal Requests

Requests under the rate limit are processed normally by your app.

## Examples

### API Server

Protect public API from abuse:
```python
from pounce import run, ServerConfig

config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=100.0,  # 100 req/s per user
    rate_limit_burst=200,                   # Allow bursts
)

run("myapi:app", config=config)
```

### High-Traffic Service

Conservative limits for high traffic:
```python
config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=50.0,  # Lower sustained rate
    rate_limit_burst=100,                  # Moderate burst
    max_connections=5000,                  # Connection limit
)
```

### Microservice

Lenient limits for internal services:
```python
config = ServerConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=1000.0,  # High rate
    rate_limit_burst=5000,                   # Large burst
)
```

## Best Practices

### Choosing Rate Limits

Consider your app's requirements:

**Conservative (public APIs):**
- Rate: 10-50 req/s per IP
- Burst: 2-5x rate
- Protects against abuse

**Moderate (web apps):**
- Rate: 50-100 req/s per IP
- Burst: 2x rate
- Balances UX and protection

**Lenient (internal services):**
- Rate: 100-1000 req/s per IP
- Burst: 5-10x rate
- Minimal impact on legitimate traffic

### Monitoring

Track rate limiting effectiveness:

1. **429 Response Count** - How many requests are blocked
2. **Per-IP Statistics** - Identify abusive IPs
3. **Burst Usage** - Understand traffic patterns

Use Prometheus metrics (Phase 6.1) to monitor:
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

### Production Deployment

**Kubernetes:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pounce-api
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: pounce
        env:
        - name: POUNCE_RATE_LIMIT_ENABLED
          value: "true"
        - name: POUNCE_RATE_LIMIT_RPS
          value: "100.0"
        - name: POUNCE_RATE_LIMIT_BURST
          value: "200"
```

**Docker Compose:**
```yaml
services:
  api:
    image: myapi:latest
    command: pounce run --rate-limit-enabled --rate-limit-rps 100
    environment:
      - POUNCE_RATE_LIMIT_ENABLED=true
      - POUNCE_RATE_LIMIT_RPS=100
      - POUNCE_RATE_LIMIT_BURST=200
```

## Advanced Usage

### Proxy Considerations

When behind a proxy (nginx, HAProxy), rate limiting may see the proxy IP instead of client IP.

**Solution:** Use `trusted_hosts` to extract real client IP:
```python
config = ServerConfig(
    rate_limit_enabled=True,
    trusted_hosts=("X-Forwarded-For", "X-Real-IP"),
)
```

⚠️ **Security:** Only enable `trusted_hosts` when you control the proxy!

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

For multi-server deployments, consider:

1. **Redis-based rate limiting** - Shared state across servers
2. **Sticky sessions** - Route same IP to same server
3. **Per-server limits** - Each server enforces independently

Pounce's built-in rate limiting is per-server. For distributed limiting, use Redis:

```python
# Custom Redis-backed rate limiter (example)
import redis

class RedisRateLimiter:
    def __init__(self, redis_url: str, rate: float, burst: int):
        self.redis = redis.from_url(redis_url)
        self.rate = rate
        self.burst = burst

    def check_rate_limit(self, ip: str) -> bool:
        key = f"ratelimit:{ip}"
        # Use Redis INCR with TTL for rate limiting
        count = self.redis.incr(key)
        if count == 1:
            self.redis.expire(key, int(1.0 / self.rate * self.burst))
        return count <= self.burst
```

## Performance Impact

Rate limiting adds minimal overhead:
- **~5-10μs per request** - Token bucket check
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

### Memory Growth

If memory grows over time:

1. **Check cleanup** - Should run every 5 minutes
2. **Monitor buckets** - Use `len(limiter._buckets)` in metrics
3. **Adjust cleanup interval** - Modify `_cleanup_interval` if needed

## Architecture

### Components

```
Client Request
     ↓
[Rate Limit Wrapper] ← Per-IP token bucket check
     ↓
   429 or Allow
     ↓
[Your ASGI App]
     ↓
Response
```

### Thread Safety

The rate limiter is thread-safe for free-threading mode:
- `RateLimiter._lock` protects bucket dictionary
- `TokenBucket._lock` protects token state
- Multiple workers can share same limiter instance

### Integration Points

Rate limiting integrates at the ASGI middleware layer:
1. Server wraps app with `create_rate_limit_wrapper()`
2. Wrapper intercepts HTTP requests
3. Checks client IP against rate limiter
4. Returns 429 or passes to app

## What's Next?

**Phase 6.3: Request Queuing & Load Shedding** 🚀

Next feature adds:
- Request queue with max depth
- 503 responses when overloaded
- Queue depth monitoring
- Dynamic queue management

Rate limiting **prevents** overload. Request queuing **handles** overload gracefully.

---

**See Also:**
- [Prometheus Metrics](prometheus-metrics.md) - Monitor rate limiting effectiveness
- [Graceful Shutdown](graceful-shutdown.md) - Handle in-flight rate limited requests
- [Lifecycle Logging](../features/lifecycle-logging.md) - Log rate limit violations
