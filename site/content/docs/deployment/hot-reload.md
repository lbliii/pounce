---
title: Hot Reload
description: Zero-downtime code deployment with graceful worker replacement
draft: false
weight: 65
lang: en
type: doc
tags: [deployment, hot-reload, zero-downtime, SO_REUSEPORT]
keywords: [hot reload, zero-downtime, worker replacement, SO_REUSEPORT, file watching]
category: how-to
---

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
- Linux 3.9+
- macOS 10.9+
- FreeBSD 12+
- Windows (falls back to restart with brief downtime)

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

- True zero-downtime reload
- Code reimport without restart
- Instant worker replacement
- Shared memory between workers

```python
config = ServerConfig(
    workers=4,  # Use threads by default
)
```

**Reload process:**
1. Supervisor reimports application code
2. New workers start with new code
3. Old workers drain gracefully
4. Zero dropped connections

### Process Mode (Fallback)

- Process isolation
- Falls back to restart_all_workers()
- Brief downtime during reload (~100ms)

**Reload process:**
1. Stop all workers
2. Wait for graceful shutdown
3. Start new workers
4. Brief gap where no requests are accepted

## Examples

### Production API

```python
from pounce import run, ServerConfig

config = ServerConfig(
    host="0.0.0.0",
    port=8000,
    workers=8,  # Multiple workers for zero-downtime
    reload_timeout=60.0,  # Allow long-running requests to finish
    metrics_enabled=True,
    rate_limit_enabled=True,
)

run("api:app", config=config)
```

### Development Server

```python
config = ServerConfig(
    reload=True,  # Watch for changes
    reload_include=(".html", ".css", ".js"),  # Watch templates too
    reload_dirs=("templates", "static"),  # Watch extra dirs
    workers=2,  # Use multiple workers even in dev
)
```

### Kubernetes Deployment

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
+-------------+
|  Worker 1   | -> :8000  (gen 1)
|  Worker 2   | -> :8000  (gen 1)
+-------------+

During Reload:
+-------------+   +-------------+
|  Worker 1   | -> :8000  (gen 1 - draining)
|  Worker 2   | -> :8000  (gen 1 - draining)
+-------------+   +-------------+
                  +-------------+
                  |  Worker 3   | -> :8000  (gen 2 - active)
                  |  Worker 4   | -> :8000  (gen 2 - active)
                  +-------------+

After Reload:
+-------------+
|  Worker 3   | -> :8000  (gen 2)
|  Worker 4   | -> :8000  (gen 2)
+-------------+
```

### Kernel Load Balancing

The kernel distributes connections across sockets:
- New connections -> Active workers (generation N+1)
- Existing connections -> Draining workers (generation N)
- Fair distribution across workers

## Best Practices

### Choose Appropriate Timeout

**Fast APIs (< 1 second response time):**
```python
config = ServerConfig(reload_timeout=10.0)
```

**Long-running requests (5-30 seconds):**
```python
config = ServerConfig(reload_timeout=60.0)
```

**Streaming/WebSocket (long-lived):**
```python
config = ServerConfig(reload_timeout=300.0)
```

### Multi-Worker Deployment

Use enough workers for overlap:

```python
# Minimum 2 workers for zero-downtime
config = ServerConfig(
    workers=max(2, cpu_count()),
)
```

### Monitor Reloads

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

## Troubleshooting

### Workers Not Draining

1. **Check reload_timeout:**
```python
config = ServerConfig(reload_timeout=60.0)
```

2. **Check for long-running requests:**
```python
config = ServerConfig(request_timeout=30.0)
```

3. **Monitor worker status** — check logs for "Worker N did not become idle" warnings

### SO_REUSEPORT Not Available

1. **Check platform:**
```python
from pounce._hot_reload import get_reload_status

status = get_reload_status()
print(status)  # {"supported": False, ...}
```

2. **Upgrade OS:** Linux 3.9+, macOS 10.9+, FreeBSD 12+

3. **Accept brief downtime:** Single worker mode falls back to restart_all_workers()

### Connections Dropped

1. **Increase workers** — more workers = smoother handoff
2. **Increase timeout** — more time to drain
3. **Check logs** — look for force-termination warnings

## Performance Impact

Hot reload adds minimal overhead:
- **Normal operation:** 0 overhead (reload system dormant)
- **During reload:** ~100-500ms transition time
- **Memory:** +~1 MB per worker generation during transition
- **CPU:** Minimal (coordination only)

## See Also

- [Graceful Reload](./graceful-reload) — SIGHUP-based rolling restart
- [Graceful Shutdown](./graceful-shutdown) — Clean shutdown coordination
- [Observability](./observability) — Track worker events
