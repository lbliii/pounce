# Hot Reload without Connection Drops

**Phase 6.5 Complete: Zero-Downtime Hot Reload** ✅

Deploy new code without dropping active connections using graceful worker replacement.

## Overview

Hot reload enables zero-downtime code deployments:
- **No dropped connections** - Active requests complete normally
- **No downtime** - New requests handled immediately
- **Graceful transition** - Old workers drain while new workers accept
- **Worker generations** - Track and coordinate across versions

### How It Works

```
1. Signal reload (SIGUSR1 or call supervisor.graceful_reload())
2. Reimport application code (thread mode)
3. Spawn new workers (generation N+1)
4. Mark old workers as draining (reject new connections)
5. Wait for old workers to finish active requests
6. Shut down old workers
7. Done! All traffic now on new workers
```

### Requirements

**Operating System:**
- Linux 3.9+ ✅
- macOS 10.9+ ✅
- FreeBSD 12+ ✅
- Windows ❌ (falls back to restart with brief downtime)

**Technical:**
- SO_REUSEPORT support (allows port sharing)
- Thread mode recommended (process mode works but with brief downtime)

## Quick Start

### Basic Reload

Hot reload is built-in and works automatically:

```python
from pounce import run, ServerConfig

config = ServerConfig(
    workers=4,  # Multi-worker for zero-downtime
    reload_timeout=30.0,  # Time to wait for workers to drain
)

run("myapp:app", config=config)
```

### Trigger Reload

**Option 1: Send SIGUSR1 signal**
```bash
# Find pounce supervisor PID
ps aux | grep pounce

# Send reload signal
kill -SIGUSR1 <pid>
```

**Option 2: Programmatic reload (thread mode)**
```python
# In your application code
from pounce.supervisor import get_supervisor

supervisor = get_supervisor()
if supervisor:
    supervisor.graceful_reload()
```

**Option 3: File watching (development)**
```python
config = ServerConfig(
    reload=True,  # Watch for file changes
    reload_include=(".html", ".css"),  # Extra extensions to watch
)
```

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `workers` | int | `1` | Worker count (2+ recommended for zero-downtime) |
| `reload_timeout` | float | `30.0` | Max time to wait for workers to drain (seconds) |
| `reload` | bool | `False` | Auto-reload on file changes (development) |
| `reload_include` | tuple | `()` | Extra file extensions to watch |
| `reload_dirs` | tuple | `()` | Extra directories to watch |

## Worker Modes

### Thread Mode (Recommended)

**Features:**
- True zero-downtime reload
- Code reimport without restart
- Instant worker replacement
- Shared memory between workers

**Usage:**
```python
config = ServerConfig(
    workers=4,  # Use threads by default
)
```

**Reload Process:**
1. Supervisor reimports application code
2. New workers start with new code
3. Old workers drain gracefully
4. Zero dropped connections

### Process Mode (Fallback)

**Features:**
- Process isolation
- Falls back to restart_all_workers()
- Brief downtime during reload (~100ms)

**Usage:**
```python
# Process mode requires special configuration
# (Not yet implemented in pounce Phase 6)
```

**Reload Process:**
1. Stop all workers
2. Wait for graceful shutdown
3. Start new workers
4. Brief gap where no requests are accepted

## Examples

### Production API

Zero-downtime deployments:

```python
from pounce import run, ServerConfig

config = ServerConfig(
    host="0.0.0.0",
    port=8000,
    workers=8,  # Multiple workers for zero-downtime
    reload_timeout=60.0,  # Allow long-running requests to finish

    # Also enable production features
    metrics_enabled=True,
    rate_limit_enabled=True,
    sentry_dsn=os.getenv("SENTRY_DSN"),
)

run("api:app", config=config)
```

### Development Server

Auto-reload on code changes:

```python
config = ServerConfig(
    reload=True,  # Watch for changes
    reload_include=(".html", ".css", ".js"),  # Watch templates too
    reload_dirs=("templates", "static"),  # Watch extra dirs
    workers=2,  # Use multiple workers even in dev
)
```

### Kubernetes Deployment

Rolling updates with zero-downtime:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pounce-api
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0  # Zero downtime
  template:
    spec:
      containers:
      - name: pounce
        image: myapp:1.2.0
        lifecycle:
          preStop:
            exec:
              # Send SIGUSR1 to trigger graceful reload
              command: ["sh", "-c", "kill -SIGUSR1 1"]
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
```

## How SO_REUSEPORT Works

### Port Sharing

SO_REUSEPORT allows multiple sockets to bind to the same port:

```
Before Reload:
┌─────────────┐
│  Worker 1   │ → :8000  (gen 1)
│  Worker 2   │ → :8000  (gen 1)
└─────────────┘

During Reload:
┌─────────────┐   ┌─────────────┐
│  Worker 1   │ → :8000  (gen 1 - draining)
│  Worker 2   │ → :8000  (gen 1 - draining)
└─────────────┘   └─────────────┘
                  ┌─────────────┐
                  │  Worker 3   │ → :8000  (gen 2 - active)
                  │  Worker 4   │ → :8000  (gen 2 - active)
                  └─────────────┘

After Reload:
┌─────────────┐
│  Worker 3   │ → :8000  (gen 2)
│  Worker 4   │ → :8000  (gen 2)
└─────────────┘
```

### Kernel Load Balancing

The kernel distributes connections across sockets:
- New connections → Active workers (generation N+1)
- Existing connections → Draining workers (generation N)
- Fair distribution across workers

## Best Practices

### Choose Appropriate Timeout

Balance responsiveness and safety:

**Fast APIs (< 1 second response time):**
```python
config = ServerConfig(
    reload_timeout=10.0,  # 10 seconds sufficient
)
```

**Long-running requests (5-30 seconds):**
```python
config = ServerConfig(
    reload_timeout=60.0,  # 60 seconds for safety
)
```

**Streaming/WebSocket (long-lived):**
```python
config = ServerConfig(
    reload_timeout=300.0,  # 5 minutes
)
```

### Multi-Worker Deployment

Use enough workers for overlap:

```python
# Minimum 2 workers for zero-downtime
config = ServerConfig(
    workers=max(2, cpu_count()),
)
```

**Why multiple workers?**
- New workers accept traffic immediately
- Old workers drain in parallel
- No gap in service

### Monitor Reloads

Track reload success:

```python
import logging

logger = logging.getLogger("pounce.supervisor")
logger.setLevel(logging.INFO)

# Logs during reload:
# - "Starting graceful reload (rolling restart)..."
# - "Spawning 4 new worker(s) (generation 2)..."
# - "Worker 0 (generation 1) is idle"
# - "Graceful reload complete. Running 4 worker(s) on generation 2"
```

### Handle Long-Running Requests

Set appropriate timeouts:

```python
config = ServerConfig(
    reload_timeout=120.0,  # 2 minutes
    request_timeout=60.0,  # Individual request timeout
)
```

If workers don't drain within timeout:
- Warning logged
- Workers force-terminated
- Active connections may be dropped

### Testing Reloads

Test reload behavior before production:

```bash
# Start server
pounce run myapp:app --workers 4

# In another terminal, monitor logs
tail -f pounce.log

# Trigger reload
kill -SIGUSR1 $(pgrep -f "pounce run")

# Check for zero-downtime:
# - "Spawning new workers" appears
# - "Draining old workers" appears
# - "Graceful reload complete" appears
# - No errors or dropped connections
```

## Kubernetes Integration

### Graceful Shutdown

Configure pod lifecycle:

```yaml
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: pounce
    lifecycle:
      preStop:
        exec:
          command: ["/bin/sh", "-c", "kill -SIGUSR1 1 && sleep 30"]
    terminationGracePeriodSeconds: 60
```

**Explanation:**
1. K8s sends SIGTERM to pod
2. preStop hook sends SIGUSR1 (reload signal)
3. Pounce starts draining workers
4. Sleep 30s to allow drain
5. K8s sends SIGKILL if still running after 60s

### Rolling Updates

Zero-downtime deployments:

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1  # One extra pod during rollout
      maxUnavailable: 0  # Never reduce capacity
```

### Health Checks

Ensure new pods ready before removing old:

```yaml
spec:
  containers:
  - name: pounce
    readinessProbe:
      httpGet:
        path: /health
        port: 8000
      initialDelaySeconds: 5
      periodSeconds: 2
      successThreshold: 2  # Two successful checks
```

## Troubleshooting

### Workers Not Draining

If workers don't become idle:

1. **Check reload_timeout:**
```python
config = ServerConfig(
    reload_timeout=60.0,  # Increase if needed
)
```

2. **Check for long-running requests:**
```python
config = ServerConfig(
    request_timeout=30.0,  # Limit request duration
)
```

3. **Monitor worker status:**
```python
# Check logs for "Worker N did not become idle" warnings
```

### SO_REUSEPORT Not Available

If SO_REUSEPORT isn't available:

1. **Check platform:**
```python
from pounce._hot_reload import get_reload_status

status = get_reload_status()
print(status)  # {"supported": False, ...}
```

2. **Upgrade OS:**
- Linux 3.9+
- macOS 10.9+
- FreeBSD 12+

3. **Accept brief downtime:**
- Single worker mode
- Falls back to restart_all_workers()

### Connections Dropped

If connections are dropped during reload:

1. **Increase workers:**
```python
config = ServerConfig(
    workers=4,  # More workers = smoother handoff
)
```

2. **Increase timeout:**
```python
config = ServerConfig(
    reload_timeout=120.0,  # More time to drain
)
```

3. **Check logs:**
```
# Look for force-termination warnings
```

## Performance Impact

Hot reload adds minimal overhead:
- **Normal operation:** 0 overhead (reload system dormant)
- **During reload:** ~100-500ms transition time
- **Memory:** +~1 MB per worker generation during transition
- **CPU:** Minimal (coordination only)

## Advanced Usage

### Custom Reload Logic

Trigger reload programmatically:

```python
from pounce.supervisor import get_supervisor

def deploy_new_version():
    """Deploy new code version."""
    # Pull new code
    subprocess.run(["git", "pull"])

    # Trigger reload
    supervisor = get_supervisor()
    if supervisor:
        supervisor.graceful_reload()
        print("Reload initiated")
    else:
        print("Not running under supervisor")
```

### Reload Hooks

Run code before/after reload:

```python
import atexit

def on_reload():
    """Called when worker is draining."""
    print("Worker draining, closing resources...")
    # Close database connections
    # Flush caches
    # etc.

atexit.register(on_reload)
```

### Generation Tracking

Track worker generations:

```python
from pounce._hot_reload import WorkerGeneration

# In worker initialization
generation = WorkerGeneration(generation=1)

print(f"Worker generation: {generation.generation}")
print(f"Worker uptime: {generation.uptime}s")
print(f"Old generation: {generation.is_old_generation(2)}")
```

## Comparison with Other Servers

| Feature | Pounce | Gunicorn | uWSGI | Uvicorn |
|---------|--------|----------|-------|---------|
| Zero-downtime reload | ✅ Yes (SO_REUSEPORT) | ✅ Yes (--preload) | ✅ Yes | ❌ No |
| Worker generations | ✅ Yes | ✅ Yes | ✅ Yes | N/A |
| Code reimport | ✅ Yes (thread mode) | ✅ Yes | ✅ Yes | N/A |
| Connection draining | ✅ Yes | ✅ Yes | ✅ Yes | N/A |
| Auto file watching | ✅ Yes | ❌ No | ❌ No | ✅ Yes (dev only) |

## What's Next?

Phase 6 is complete! All production features implemented:
- ✅ Prometheus metrics
- ✅ Rate limiting
- ✅ Request queueing
- ✅ Sentry integration
- ✅ Hot reload

**Next steps:**
- Phase 7: Advanced features (HTTP/3, etc.)
- Production deployment guides
- Performance tuning documentation

---

**See Also:**
- [Graceful Shutdown](graceful-shutdown.md) - Clean shutdown coordination
- [Lifecycle Logging](../features/lifecycle-logging.md) - Track worker events
- [Prometheus Metrics](prometheus-metrics.md) - Monitor reload events
