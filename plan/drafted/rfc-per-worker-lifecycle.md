# RFC: Per-Worker Lifecycle Scopes

**Status**: Draft
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
await app(
    {"type": "pounce.worker.startup", "worker_id": self._worker_id},
    _noop_receive,
    _noop_send,
)

# After server.close() (on worker's event loop):
await app(
    {"type": "pounce.worker.shutdown", "worker_id": self._worker_id},
    _noop_receive,
    _noop_send,
)
```

These are fire-and-forget scope invocations — no request/response protocol. The `receive` and `send` callables are no-ops because no data exchange is needed.

### Why Scope Invocations, Not Callbacks

- Uses the existing ASGI `__call__` interface — no new API surface on pounce.
- Any ASGI middleware or framework can intercept these by checking `scope["type"]`.
- Non-pounce servers simply never send these scopes — apps degrade gracefully.
- Consistent with how lifespan works: server sends scopes, app responds.

### Error Handling

- If the app raises during `pounce.worker.startup`, the worker logs the error and **does not accept connections**. This prevents a broken worker from silently serving requests with uninitialized state.
- If the app raises during `pounce.worker.shutdown`, the worker logs the error and continues shutdown. Cleanup failures should not prevent worker exit.
- Apps that don't recognize these scope types will typically raise or return silently — both are handled gracefully.

---

## Implementation Plan

### Changes to `Worker._serve()` (`worker.py`)

```python
async def _serve(self) -> None:
    self._loop = asyncio.get_running_loop()
    self._async_shutdown = asyncio.Event()

    # --- NEW: Per-worker startup ---
    try:
        await self._app(
            {"type": "pounce.worker.startup", "worker_id": self._worker_id},
            _noop_receive,
            _noop_send,
        )
    except Exception:
        self._logger.exception(
            "Worker %d startup hook failed", self._worker_id
        )
        return  # Don't accept connections with broken state

    server = await asyncio.start_server(...)
    ...

    # (existing shutdown logic)
    finally:
        ...
        server.close()
        await server.wait_closed()

        # --- NEW: Per-worker shutdown ---
        try:
            await self._app(
                {"type": "pounce.worker.shutdown", "worker_id": self._worker_id},
                _noop_receive,
                _noop_send,
            )
        except Exception:
            self._logger.exception(
                "Worker %d shutdown hook failed", self._worker_id
            )

        self._logger.info("Worker %d stopped", self._worker_id)
```

### Module-level helpers

```python
async def _noop_receive() -> dict[str, Any]:
    """No-op receive for worker lifecycle scopes."""
    await asyncio.Event().wait()  # Block forever — should never be called
    return {}

async def _noop_send(message: dict[str, Any]) -> None:
    """No-op send for worker lifecycle scopes."""
    pass
```

### Tests

1. **`test_worker_startup_scope_sent`** — Verify the app receives `pounce.worker.startup` with correct `worker_id` before connections are accepted.
2. **`test_worker_shutdown_scope_sent`** — Verify the app receives `pounce.worker.shutdown` after the server closes.
3. **`test_worker_startup_failure_prevents_serving`** — Verify that if the startup scope raises, the worker does not accept connections.
4. **`test_worker_shutdown_failure_non_fatal`** — Verify shutdown errors are logged but don't prevent worker exit.
5. **`test_unrecognized_scope_handled_gracefully`** — Verify apps that raise on unknown scope types don't crash the worker.

---

## Migration & Compatibility

- **No breaking changes.** Existing apps ignore unknown scope types (raise or return silently) — both are handled.
- **Opt-in.** Only apps that explicitly check for `pounce.worker.startup` / `pounce.worker.shutdown` will see new behavior.
- **Framework adoption.** Chirp will add `@app.on_worker_startup` / `@app.on_worker_shutdown` decorators that dispatch from these scopes.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| App raises on unknown scope type | Medium | Catch all exceptions, log, and proceed (startup: don't serve; shutdown: continue) |
| Startup hook is slow, delays worker ready | Low | Log timing; consider adding a timeout in future |
| Scope type name conflicts with other servers | Low | Prefixed with `pounce.` to namespace |

---

## Open Questions

1. Should the scope include additional metadata (e.g., `worker_count`, `worker_mode`)?
2. Should there be a configurable timeout for worker startup hooks?
3. Should the scope type be `pounce.worker.startup` or a more generic `worker.startup`?
