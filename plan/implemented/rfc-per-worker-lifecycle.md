# RFC: Per-Worker Lifecycle Scopes

**Status**: Implemented
**Date**: 2026-02-08
**Scope**: `pounce/worker.py`

---

## Problem

Pounce runs ASGI lifespan once in the main thread before workers spawn (`server.py:375`). Each worker thread runs its own asyncio event loop (`worker.py:152`). Any async resource created during lifespan (httpx clients, DB connection pools, aiohttp sessions) binds internal asyncio primitives to the lifespan loop — not the worker's loop.

When a worker tries to use those resources, the asyncio primitives throw:

```
RuntimeError: <asyncio.locks.Event object at 0x...> is bound to a different event loop
```

### Evidence

**Lifespan runs on the main thread, once:**

```
src/pounce/server.py:375 — async with run_lifespan(self._app, self._config):
src/pounce/server.py:285-286 — "Lifespan runs once in the main process/thread
                                 before workers are spawned. Workers do not run lifespan."
```

**Workers create their own event loops:**

```
src/pounce/worker.py:152 — asyncio.run(self._serve())
src/pounce/worker.py:156 — self._loop = asyncio.get_running_loop()
```

**No per-worker lifecycle hooks exist:**

```
src/pounce/worker.py:154-191 — _serve() goes straight to asyncio.start_server()
                                 with no pre/post hook mechanism.
```

### Impact

Every ASGI app using async libraries (httpx, aiohttp, asyncpg, motor, etc.) under pounce multi-worker will hit this. The current workaround — per-event-loop resource pools with `id(loop)` keying — is fragile, has no cleanup path, and must be reimplemented in every app.

---

## Goals

1. Give ASGI apps a hook that runs **on each worker's event loop** before it accepts connections.
2. Give ASGI apps a hook that runs **on each worker's event loop** after it stops accepting connections.
3. Zero overhead on the hot path (request handling is unchanged).
4. No coupling to specific frameworks — any ASGI app can handle these scopes.

### Non-Goals

- Changing the global lifespan protocol (it still runs once, as designed).
- Providing a worker-state injection mechanism (frameworks handle that).
- Per-request worker identification (separate concern).

---

## Design

### Worker Lifecycle Scopes

In `Worker._serve()`, send two ASGI scope invocations to the app — one before accepting connections, one after shutdown:

```python
# Before accepting connections (on worker's event loop):
await asyncio.wait_for(
    app(
        {"type": "pounce.worker.startup", "worker_id": self._worker_id},
        _worker_lifecycle_receive,
        _worker_lifecycle_send,
    ),
    timeout=30.0,
)

# After server.close() (on worker's event loop):
await asyncio.wait_for(
    app(
        {"type": "pounce.worker.shutdown", "worker_id": self._worker_id},
        _worker_lifecycle_receive,
        _worker_lifecycle_send,
    ),
    timeout=10.0,
)
```

### Receive and Send Helpers

The `_worker_lifecycle_receive` callable returns `{"type": "http.disconnect"}` immediately. This handles a common edge case: apps that pass unrecognised scope types to their HTTP handler, which calls `receive()` and would otherwise block forever. The disconnect message causes the handler to return quickly.

The `_worker_lifecycle_send` callable is a no-op — lifecycle scopes produce no response.

### Timeout Protection

Both startup (30s) and shutdown (10s) scopes are wrapped in `asyncio.wait_for`. If the app doesn't recognise the scope and blocks despite the disconnect-returning receive, the timeout fires and the worker proceeds normally. This is the safety net for apps that cannot handle unknown scope types at all.

### Why Scope Invocations, Not Callbacks

- Uses the existing ASGI `__call__` interface — no new API surface on pounce.
- Any ASGI middleware or framework can intercept these by checking `scope["type"]`.
- Non-pounce servers simply never send these scopes — apps degrade gracefully.
- Consistent with how lifespan works: server sends scopes, app responds.

### Error Handling

- If the app raises during `pounce.worker.startup`, the worker logs the error and **does not accept connections**. This prevents a broken worker from silently serving requests with uninitialized state.
- If the app raises during `pounce.worker.shutdown`, the worker logs the error and continues shutdown. Cleanup failures should not prevent worker exit.
- If the app times out on either scope, the worker proceeds normally (the app doesn't understand the scope type).

---

## Tests

1. **`test_startup_scope_sent_before_connections`** — Verify the app receives `pounce.worker.startup` with correct `worker_id` before connections are accepted.
2. **`test_shutdown_scope_sent_after_close`** — Verify the app receives `pounce.worker.shutdown` after the server closes.
3. **`test_startup_and_shutdown_both_sent`** — Both scopes fire in correct order.
4. **`test_startup_failure_prevents_serving`** — If the startup scope raises, the worker does not accept connections.
5. **`test_shutdown_failure_non_fatal`** — Shutdown errors are logged but don't prevent worker exit.
6. **`test_app_raises_on_unknown_scope`** — Apps that raise on unknown scope types don't crash the worker.

Implemented proof references:

- `tests/unit/test_worker_lifecycle.py` - per-worker startup/shutdown scope behavior.
- `docs/design/subinterpreter-workers.md` - worker startup hook guidance for loop-local resources.
- `CHANGELOG.md` - release notes document per-worker lifecycle scopes and test coverage.

---

## Migration & Compatibility

- **No breaking changes.** Existing apps ignore unknown scope types (raise or return silently) — both are handled.
- **Opt-in.** Only apps that explicitly check for `pounce.worker.startup` / `pounce.worker.shutdown` will see new behavior.
- **Framework adoption.** Chirp will add `@app.on_worker_startup` / `@app.on_worker_shutdown` decorators that dispatch from these scopes.

---

## Resolved Questions

1. **Additional metadata in scope?** — Not for now. `worker_id` is sufficient. Adding `worker_count` or `worker_mode` can be done later without breaking changes.
2. **Configurable timeout?** — Yes, implemented with fixed timeouts (30s startup, 10s shutdown). Making them user-configurable via `ServerConfig` can follow if needed.
3. **Scope type naming?** — Kept `pounce.worker.startup` / `pounce.worker.shutdown` with the `pounce.` prefix to avoid namespace conflicts with other servers.
