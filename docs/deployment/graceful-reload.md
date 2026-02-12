# Graceful Worker Reload

Pounce supports **zero-downtime code reloads** via SIGHUP signal, enabling you to deploy new code without dropping any in-flight requests.

## Overview

When you send SIGHUP to a running pounce server, it performs a **rolling restart**:

1. **Keep serving**: Old workers continue handling existing requests
2. **Spawn new generation**: New workers start with fresh code
3. **Drain old workers**: Old workers finish current requests, reject new ones
4. **Seamless handoff**: Once drained, old workers shut down

**Zero requests dropped.** No connection refused errors.

## Basic Usage

### Send SIGHUP Signal

```bash
# Find the pounce process ID
ps aux | grep pounce

# Send SIGHUP to trigger reload
kill -HUP <pid>
```

### With systemd

```bash
# Reload the service
systemctl reload pounce
```

Your `pounce.service` file should use `ExecReload`:

```ini
[Service]
Type=notify
ExecStart=/usr/bin/pounce myapp:app --workers=4
ExecReload=/bin/kill -HUP $MAINPID
```

### With Supervisor

```ini
[program:pounce]
command=/usr/bin/pounce myapp:app --workers=4
autorestart=true
killasgroup=true
```

```bash
# Reload via supervisor
supervisorctl signal HUP pounce
```

## Configuration

Control drain timeout with `reload_timeout` (default: 30 seconds):

```python
from pounce import ServerConfig

config = ServerConfig(
    reload_timeout=60.0,  # Allow up to 60s for workers to drain
    workers=4,
)
```

If workers haven't drained after `reload_timeout`, they are **force-stopped**.

## Thread Mode vs Process Mode

### Thread Mode (Python 3.14t, nogil) ✅ Recommended

Zero-downtime rolling restart fully supported:

```python
config = ServerConfig(workers=4)  # Uses threads on nogil Python
```

- Old and new workers run simultaneously
- True zero-downtime reload
- Automatic code reimport

### Process Mode (GIL builds) ⚠️ Limited

Falls back to **hard restart** (brief downtime):

- All workers stop before new ones start
- ~100-500ms of downtime depending on drain speed
- Still safer than kill+restart

**Recommendation:** Use thread mode (Python 3.14t) for production zero-downtime reloads.

## How It Works

### Rolling Restart Flow

```
Time 0s:   [Worker-0] [Worker-1] [Worker-2] [Worker-3]  (Generation 0)
           ↓ SIGHUP received

Time 0.1s: [Worker-0] [Worker-1] [Worker-2] [Worker-3]  (Gen 0, draining)
           [Worker-4] [Worker-5] [Worker-6] [Worker-7]  (Gen 1, accepting)

Time 2s:   Worker-0, Worker-1 finish requests and exit
           [Worker-2] [Worker-3]  (Gen 0, draining)
           [Worker-4] [Worker-5] [Worker-6] [Worker-7]  (Gen 1, accepting)

Time 5s:   Worker-2, Worker-3 finish and exit
           [Worker-4] [Worker-5] [Worker-6] [Worker-7]  (Gen 1, accepting)
           ✅ Reload complete!
```

### Drain Mode

When a worker enters **drain mode**:

- ❌ **Stops accepting** new connections
- ✅ **Finishes** all in-flight requests
- ⏱️ **Waits** for active connections to complete
- 🛑 **Shuts down** once idle (or after timeout)

## Deployment Strategies

### Blue-Green Style (Zero Risk)

```bash
# 1. Deploy new code to new version directory
cp -r /app/v1 /app/v2

# 2. Reload pounce to pick up new code
kill -HUP $(cat /var/run/pounce.pid)

# 3. Old workers drain, new workers serve
# 4. No downtime, no connection errors
```

### Container Deployments (Kubernetes, Docker)

```yaml
# kubernetes deployment
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0  # Zero downtime
  containers:
  - name: pounce
    lifecycle:
      preStop:
        exec:
          command: ["/bin/sh", "-c", "kill -HUP 1; sleep 35"]
```

Pounce receives SIGHUP before SIGTERM, allowing graceful drain before pod termination.

## Monitoring Reload

### Log Output

```
[INFO] Received SIGHUP — triggering graceful reload
[INFO] Successfully reimported app from myapp:app
[INFO] Spawning 4 new worker(s) (generation 1)...
[INFO] New workers spawned. Draining old workers (generation 0)...
[INFO] Worker 0 (generation 0) is idle
[INFO] Worker 1 (generation 0) is idle
[INFO] Worker 2 (generation 0) is idle
[INFO] Worker 3 (generation 0) is idle
[INFO] Graceful reload complete. Running 4 worker(s) on generation 1
```

### Health Checks

Configure a health endpoint to verify reload success:

```python
from pounce import ServerConfig

config = ServerConfig(
    health_check_path="/health",
    workers=4,
)
```

After SIGHUP, your load balancer can verify the new generation is healthy before routing traffic.

## Troubleshooting

### Workers Not Draining

**Problem:** Workers stay active past `reload_timeout`

**Cause:** Long-running requests (uploads, WebSocket, streaming)

**Solution:**

1. Increase `reload_timeout`:
   ```python
   ServerConfig(reload_timeout=120.0)  # 2 minutes
   ```

2. Implement graceful WebSocket close in your app:
   ```python
   # Close WebSocket connections on worker shutdown signal
   @app.on_event("pounce.worker.shutdown")
   async def close_websockets():
       await websocket_manager.close_all()
   ```

### Module Reload Failures

**Problem:** `ImportError` or `AttributeError` after reload

**Cause:** Code changes break module imports

**Solution:** Fix the code error. Pounce will log the exception and continue with the **old version**:

```
[ERROR] Reload failed — continuing with previous version
```

No downtime from bad deploys!

### Process Mode Downtime

**Problem:** Brief (100-500ms) connection errors during reload

**Cause:** Process mode uses hard restart (all workers stop before new ones start)

**Solution:** Upgrade to **Python 3.14t (nogil)** for thread-based zero-downtime reloads.

## Best Practices

1. **Use Thread Mode**: Python 3.14t with nogil for true zero-downtime
2. **Set Generous Timeout**: `reload_timeout` should exceed your longest request
3. **Test Locally**: Verify reload works with `kill -HUP` before deploying
4. **Monitor Logs**: Watch for "Graceful reload complete" confirmation
5. **Health Checks**: Use `/health` endpoint to validate new generation
6. **Automate**: Integrate SIGHUP into your CI/CD pipeline

## Comparison with Other Servers

| Server | Zero-Downtime Reload | Method |
|--------|---------------------|--------|
| **pounce** | ✅ Yes (thread mode) | SIGHUP rolling restart |
| Uvicorn | ❌ No | Must use external orchestrator |
| Gunicorn | ⚠️ Partial (brief downtime) | HUP restarts master process |
| Hypercorn | ❌ No | Manual stop/start required |

Pounce is the **only** Python ASGI server with true zero-downtime rolling restart built-in.

## See Also

- [Enhanced Connection Draining](../features/connection-draining.md) — Clean shutdown behavior
- [Production Deployment](./production.md) — Full deployment guide
- [Structured Logging](../features/structured-logging.md) — Monitor reload events
