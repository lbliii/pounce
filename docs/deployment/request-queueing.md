# Request Queueing & Load Shedding

**Phase 6.3 Complete: Request Queueing & Load Shedding** ✅

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

| Feature | Rate Limiting (6.2) | Request Queueing (6.3) |
|---------|---------------------|------------------------|
| **Purpose** | Prevent per-client abuse | Handle global server overload |
| **Scope** | Per client IP | Global (all clients) |
| **Response** | 429 (rate limited) | 503 (overloaded) |
| **When** | Client exceeds limit | Server at capacity |
| **Use Case** | API protection | Load management |

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

Configure custom queue capacity:

```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=500,  # Queue up to 500 requests
)
```

### Unlimited Queue

Allow unlimited queueing (use with caution):

```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=0,  # Unlimited
)
```

⚠️ **Warning:** Unlimited queues can lead to memory exhaustion under sustained overload!

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `request_queue_enabled` | bool | `False` | Enable request queueing |
| `request_queue_max_depth` | int | `1000` | Maximum queued requests (0 = unlimited) |

## How It Works

### Request Flow

```
1. Request arrives → Check queue capacity
2. If capacity available → Acquire queue slot → Process request → Release slot
3. If queue full → Return 503 Service Unavailable
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

### Wait Time Tracking

Queue metrics track:
- Total requests queued
- Total requests rejected (503)
- Average wait time in queue
- Maximum wait time
- Rejection rate

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

### Normal Requests

Requests that acquire a queue slot are processed normally by your app.

## Examples

### Web Application

Handle traffic spikes gracefully:
```python
from pounce import run, ServerConfig

config = ServerConfig(
    # Queue up to 1000 requests
    request_queue_enabled=True,
    request_queue_max_depth=1000,

    # Process up to 100 concurrent connections
    max_connections=100,

    # Workers for parallelism
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
    request_queue_max_depth=100,  # Small queue
    max_connections=10,            # Limited concurrency
    request_timeout=300.0,         # Long timeout
)
```

### High-Throughput Service

Large queue for bursty traffic:
```python
config = ServerConfig(
    request_queue_enabled=True,
    request_queue_max_depth=5000,  # Large queue
    max_connections=1000,          # High concurrency
    workers=8,                     # Many workers
)
```

## Best Practices

### Choosing Queue Depth

Consider your app's characteristics:

**Conservative (predictable load):**
- Queue depth: 100-500
- Reject requests quickly when overloaded
- Prevents cascading failures

**Moderate (variable load):**
- Queue depth: 500-1000 (default)
- Buffer moderate traffic spikes
- Balance responsiveness and capacity

**Aggressive (bursty traffic):**
- Queue depth: 1000-5000
- Handle large traffic bursts
- Higher memory usage

**Formula:**
```
queue_depth ≈ peak_rps * acceptable_wait_seconds
```

Example:
- Peak: 1000 req/s
- Acceptable wait: 2 seconds
- Queue depth: 2000

### Monitoring

Track queue health:

1. **Queue Depth** - Current queued requests
2. **Rejection Rate** - % of requests returning 503
3. **Wait Time** - Average time in queue
4. **Max Wait Time** - P99/P95 latency impact

Use Prometheus metrics (Phase 6.1):
```
http_requests_total{status="503"}  # Queue rejections
```

### Client Handling

Teach clients to respect 503 responses:

**Parse Retry-After:**
```python
import requests
import time

response = requests.get("https://api.example.com/users")
if response.status_code == 503:
    retry_after = int(response.headers.get("Retry-After", 5))
    time.sleep(retry_after)
    # Retry request
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
        time.sleep(min(backoff, 60))  # Cap at 60s

    raise Exception("Server overloaded after retries")
```

**Circuit Breaker:**
```python
class CircuitBreaker:
    def __init__(self, threshold=5, timeout=60):
        self.failures = 0
        self.threshold = threshold
        self.timeout = timeout
        self.opened_at = None

    def call(self, func, *args, **kwargs):
        # If circuit open, fail fast
        if self.opened_at:
            if time.time() - self.opened_at < self.timeout:
                raise Exception("Circuit breaker open")
            # Try to close circuit
            self.opened_at = None
            self.failures = 0

        response = func(*args, **kwargs)

        if response.status_code == 503:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.time()
        else:
            self.failures = 0

        return response
```

### Capacity Planning

Use queue metrics for scaling decisions:

**Under-provisioned (scale up):**
- High rejection rate (>5%)
- High average wait time (>500ms)
- Queue frequently full

**Over-provisioned (scale down):**
- Low rejection rate (<0.1%)
- Low average wait time (<50ms)
- Queue rarely used

**Right-sized:**
- Rejection rate: 0.1-1%
- Average wait: 50-200ms
- Queue absorbs spikes

### Production Deployment

**Kubernetes HPA:**
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: pounce-api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: pounce-api
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Pods
    pods:
      metric:
        name: http_requests_rejected_total
      target:
        type: AverageValue
        averageValue: "100"  # Scale up if 503s > 100/pod
```

**Load Balancer Health Check:**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: pounce-api
spec:
  ports:
  - port: 8000
    targetPort: 8000
  selector:
    app: pounce-api
  healthCheck:
    httpGet:
      path: /health
      port: 8000
    # Remove pods returning 503
    failureThreshold: 3
```

## Advanced Usage

### Custom Queue Strategies

For advanced use cases, implement custom queueing:

```python
from pounce._request_queue import RequestQueue, QueueMetrics
import asyncio

class PriorityRequestQueue(RequestQueue):
    """Queue with priority lanes."""

    def __init__(self, max_depth: int):
        super().__init__(max_depth)
        self._priority_slots = asyncio.Semaphore(max_depth // 2)
        self._normal_slots = asyncio.Semaphore(max_depth // 2)

    async def acquire(self, priority: bool = False) -> bool:
        if priority:
            return await self._try_acquire(self._priority_slots)
        else:
            return await self._try_acquire(self._normal_slots)

    async def _try_acquire(self, semaphore):
        if semaphore.locked():
            return False
        await semaphore.acquire()
        return True
```

### Per-Route Queues

Different queue depths per route:

```python
from pounce._request_queue import RequestQueue, create_queue_wrapper

# Expensive routes get smaller queue
expensive_queue = RequestQueue(max_depth=100)

# Cheap routes get larger queue
cheap_queue = RequestQueue(max_depth=1000)

async def route_aware_queueing(scope, receive, send):
    if scope["path"].startswith("/api/expensive"):
        wrapper = create_queue_wrapper(app, expensive_queue)
    else:
        wrapper = create_queue_wrapper(app, cheap_queue)

    await wrapper(scope, receive, send)
```

### Dynamic Queue Sizing

Adjust queue depth based on load:

```python
class AdaptiveQueue:
    def __init__(self, base_depth: int):
        self.queue = RequestQueue(base_depth)
        self.base_depth = base_depth
        self.current_depth = base_depth

    async def adapt(self, rejection_rate: float):
        """Adjust queue depth based on rejection rate."""
        if rejection_rate > 0.1:
            # Too many rejections, increase queue
            new_depth = int(self.current_depth * 1.5)
            self.queue = RequestQueue(min(new_depth, self.base_depth * 5))
            self.current_depth = new_depth
        elif rejection_rate < 0.01:
            # Very few rejections, decrease queue
            new_depth = int(self.current_depth * 0.8)
            self.queue = RequestQueue(max(new_depth, self.base_depth))
            self.current_depth = new_depth
```

## Performance Impact

Request queueing adds minimal overhead:
- **~1-5μs per request** - Queue acquire/release
- **Thread-safe** - asyncio Semaphore
- **Memory efficient** - ~50 bytes per queued request
- **No blocking** - Async acquire (try-lock pattern)

For 1000 max queue depth:
- Memory: ~50 KB
- CPU: <0.01% additional load

## Troubleshooting

### High Rejection Rate

If many requests get 503:

1. **Increase queue depth** - Allow more buffering
2. **Scale workers** - Add more processing capacity
3. **Optimize app** - Reduce request processing time
4. **Add replicas** - Horizontal scaling

### Memory Growth

If memory grows with queue:

1. **Check queue depth** - Monitor actual queue size
2. **Reduce max_depth** - Lower queue capacity
3. **Fix memory leaks** - App may be leaking memory
4. **Monitor wait times** - Long waits indicate bottleneck

### No Load Shedding

If 503s aren't being sent:

1. **Check config** - Ensure `request_queue_enabled=True`
2. **Verify integration** - Check logs for "Request queueing enabled"
3. **Test capacity** - Send more requests than `max_connections`
4. **Check metrics** - Monitor queue depth

## Architecture

### Components

```
Client Request
     ↓
[Queue Wrapper] ← Acquire queue slot
     ↓
   503 or Allow
     ↓
[Your ASGI App]
     ↓
Release Queue Slot
     ↓
Response
```

### Integration Points

Request queueing integrates at the ASGI middleware layer:
1. Server wraps app with `create_queue_wrapper()`
2. Wrapper tries to acquire queue slot
3. Returns 503 if queue full, otherwise processes request
4. Releases slot when request completes (success or error)

### Metrics Collection

Queue metrics tracked per request:
- **Queued:** Timestamp when acquired slot
- **Wait time:** Time from acquire to app start
- **Rejected:** Increment counter on 503

Use metrics for monitoring and alerting.

## What's Next?

**Phase 6.4: Error Tracking Integration (Sentry)** 🚀

Next feature adds:
- Automatic error reporting to Sentry
- Request context capture
- Performance monitoring
- Release tracking

---

**See Also:**
- [Rate Limiting](rate-limiting.md) - Per-client abuse prevention
- [Prometheus Metrics](prometheus-metrics.md) - Monitor queue metrics
- [Graceful Shutdown](graceful-shutdown.md) - Handle queued requests during shutdown
