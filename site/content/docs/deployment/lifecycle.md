---
title: Server Lifecycle
description: Graceful reload and shutdown with connection draining
draft: false
weight: 60
lang: en
type: doc
tags: [deployment, reload, shutdown, rolling-reload]
keywords: [graceful reload, graceful shutdown, SIGHUP, SIGTERM, connection draining]
category: how-to
---

# Server Lifecycle

Pounce handles **SIGHUP** for graceful reload on supported multi-worker paths
and **SIGTERM** / **SIGINT** for graceful shutdown. Both paths use connection
draining: active requests get time to finish, while workers that are leaving
service reject new connections.

## Graceful Reload (SIGHUP)

On supported multi-worker thread and subinterpreter paths, send SIGHUP to
perform a rolling restart with fresh code:

```bash
kill -HUP <pid>
# or with systemd:
systemctl reload pounce
```

### What Happens

1. Old workers continue handling existing requests
2. App code is reimported and new workers spawn (generation N+1)
3. Old workers enter drain mode (finish active requests, reject new ones)
4. Once drained (or after `reload_timeout`), old workers exit

```
Time 0s:   [Worker-0] [Worker-1] [Worker-2] [Worker-3]  (Gen 0)
           SIGHUP received
Time 0.1s: [Worker-0..3 draining] [Worker-4..7 accepting]  (Gen 0+1)
Time 5s:   [Worker-4] [Worker-5] [Worker-6] [Worker-7]  (Gen 1 only)
```

If the reimport fails, pounce logs the error and continues with the old code
instead of swapping to the failed generation.

HTTP/3 uses a separate UDP/QUIC listener. Its proof ledger covers generation
rotation, bounded drain, and orphan-thread cleanup. Under-budget streams finish;
streams exceeding `shutdown_timeout` are cancelled and QUIC closes.

Current subprocess proof covers SIGTERM mixed-traffic drain and SIGHUP recovery
to serving traffic across the documented worker-mode matrix. The reproducible
drain profile also drives SIGHUP followed by SIGTERM and records in-flight
completion, bounded refusal outcomes, exit time, and orphan-worker absence.
This mode-scoped proof does not imply lossless reload across every protocol;
HTTP/3 keeps the bounded QUIC exception above rather than claiming TCP-identical
semantics.

### Configuration

```python
config = ServerConfig(
    reload_timeout=60.0,  # Max drain time (default: 30s)
    workers=4,
)
```

### systemd

```ini
[Service]
Type=notify
ExecStart=/usr/bin/pounce serve --app myapp:app --workers=4
ExecReload=/bin/kill -HUP $MAINPID
```

### File Watching (Development)

For development, enable auto-reload on file changes:

```python
config = ServerConfig(
    reload=True,
    reload_include=(".html", ".css"),  # Extra extensions
    reload_dirs=("templates",),        # Extra directories
)
```

## Graceful Shutdown (SIGTERM)

On SIGTERM or SIGINT, pounce drains connections then exits:

1. **Stops accepting** new connections immediately
2. **Finishes** active requests (up to `shutdown_timeout`)
3. **Force-terminates** work that exceeds the timeout
4. Runs per-worker `pounce.worker.shutdown` hooks
5. Completes ASGI `lifespan.shutdown`
6. Releases listeners and **exits** with status 0

```python
config = ServerConfig(
    shutdown_timeout=30.0,  # Per-worker drain time (default: 10s)
)
```

### Kubernetes

```yaml
spec:
  containers:
  - name: app
    lifecycle:
      preStop:
        exec:
          command: ["sh", "-c", "sleep 5"]  # LB de-registration delay
    readinessProbe:
      httpGet:
        path: /readyz
        port: 8000
  terminationGracePeriodSeconds: 40  # > shutdown_timeout + preStop
```

Key: `terminationGracePeriodSeconds` must exceed `shutdown_timeout` + preStop delay, or Kubernetes sends SIGKILL before drain completes.

### Docker

Use exec form so signals reach pounce directly:

```dockerfile
CMD ["pounce", "serve", "myapp:app", "--host", "0.0.0.0"]
```

### systemd

```ini
[Service]
Type=notify
KillSignal=SIGTERM
KillMode=mixed
TimeoutStopSec=40s
```

## Worker Mode Comparison

| | Thread Mode (3.14t) | Process Mode (GIL) | Subinterpreter Mode |
|---|---|---|---|
| Reload | Rolling generation swap with old + new overlap | Stop/start fallback may have a brief gap | Replacement readiness, then old-generation acceptor retirement and bounded drain |
| Shutdown | Drain per-thread | Drain per-process | IIC-coordinated bounded drain and shutdown |
| Scope | Shared-interpreter ASGI workers | Forked ASGI workers | Stable isolated ASGI web workers; import path and compatible dependencies required |

Thread mode requires Python 3.14t (free-threading). Process mode falls back to stop-all-then-start.
Subinterpreter mode is explicit. Its lifecycle is stable for ASGI web workers,
but dependency compatibility and JSON-safe lifespan-state limits still apply.

## Troubleshooting

**Workers not draining:** Increase `reload_timeout` or `shutdown_timeout`. Check
for long-lived connections (WebSocket, streaming). `request_timeout` bounds
request-body progress; application execution is bounded by lifecycle drain
deadlines rather than this network-input setting.

**SIGKILL before drain complete (Kubernetes):** Increase `terminationGracePeriodSeconds` to exceed `shutdown_timeout` + preStop delay.

**Module reload failures:** Pounce logs the import error and continues with the previous version.
