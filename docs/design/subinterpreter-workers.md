# Design: Subinterpreter Workers (PEP 734)

**Status**: Stable for ASGI web workers within the limitation matrix below.
**Date**: 2026-07-08
**Python**: 3.14.2t (free-threaded, `concurrent.interpreters` available)

---

## Summary

Pounce supports a third worker mode: `WorkerMode.SUBINTERPRETER`. Each worker
runs in a dedicated `threading.Thread` wrapping a `concurrent.interpreters`
subinterpreter. This provides thread-like performance with process-like isolation,
all in one process.

```
pounce serve --app myapp:app --workers 4 --worker-mode subinterpreter
```

The CLI supplies the import identity from `--app`. Embedded callers must pass
the same identity explicitly:

```python
Server(config, app, app_path="myapp:app")
```

Pounce validates this at construction and raises
`POUNCE_SUPERVISOR_SUBINTERPRETER_NO_APP_PATH` instead of silently selecting a
different worker model. Explicit subinterpreter mode uses the supervisor even
when `workers=1`.

---

## Architecture

```
Supervisor (main interpreter)
  |
  |-- Thread "pounce-subinterp-0"
  |     |-- Interpreter 0
  |     |     |-- asyncio.run(_run_worker_with_iic())
  |     |     |-- Worker._handle_connection()
  |     |     `-- _iic_bridge() polling ctrl_queue
  |     `-- ctrl_queue / status_queue (IIC)
  |
  |-- Thread "pounce-subinterp-1"
  |     |-- Interpreter 1
  |     `-- ...
  |
  `-- Health monitor (detects crashed workers, auto-restarts)
```

### Key files

| File | Role |
|------|------|
| `src/pounce/_runtime.py` | `WorkerMode.SUBINTERPRETER`, `has_subinterpreters()` |
| `src/pounce/config.py` | Validation, `to_iic_dict()`/`from_iic_dict()` serialization |
| `src/pounce/supervisor.py` | `_spawn_subinterpreter_worker()`, graceful reload, health monitoring |
| `src/pounce/_subinterpreter_bootstrap.py` | Bootstrap code executed inside each subinterpreter |
| `src/pounce/_cli.py` | `--worker-mode subinterpreter` CLI flag |

---

## IIC Protocol

Two `interpreters.Queue` channels per worker:

**ctrl_queue (supervisor -> worker):**

| Message | Meaning |
|---------|---------|
| `("shutdown",)` | Begin graceful shutdown |
| `("drain",)` | Stop accepting new connections, finish in-flight |

**status_queue (worker -> supervisor):**

| Message | Meaning |
|---------|---------|
| `("started",)` | Worker interpreter initialized |
| `("serving",)` | Accept loop running |
| `("draining",)` | Drain acknowledged, finishing requests |
| `("idle",)` | All connections finished, ready to close |
| `("stopped",)` | Worker exited cleanly |
| `("error", detail_str)` | Error during operation |

### UnboundQueueItem handling

When a subinterpreter is destroyed, items it placed on queues become
`UnboundQueueItem` objects (not tuples). The supervisor's `_try_iic_get()`
guards against this by checking `isinstance(msg, tuple)`.

The drain protocol avoids this by keeping the interpreter alive after
reporting `("idle",)` — it waits for an explicit `("shutdown",)` before
exiting, ensuring the supervisor can read the idle status.

---

## App Loading

Each subinterpreter imports the ASGI app fresh by module path:

```python
_import_app("myapp:app")              # Direct import
_import_app("myapp:create_app()")     # Factory pattern
```

### sys.path inheritance

Subinterpreters start with minimal `sys.path`. The parent's resolved path
is passed via `prepare_main()` as a tuple (IIC-safe), filtering out editable
install path hooks that don't transfer:

```python
parent_sys_path = tuple(
    os.path.abspath(p) if p == "" else p
    for p in sys.path
    if not p.startswith("__editable__")
)
```

---

## Config Serialization

`ServerConfig` provides IIC-safe serialization methods:

- `config.to_iic_dict()` -> dict (drops callables, middleware, display)
- `config.to_json()` -> JSON string
- `ServerConfig.from_iic_dict(d)` -> reconstructed config
- `ServerConfig.from_json(s)` -> reconstructed config

Handles `frozenset -> list` and `tuple -> list` round-tripping.

---

## Lifespan State

Strategy: main interpreter runs lifespan, IIC-safe state keys are serialized
to JSON and passed to workers. Non-serializable values (DB pools, HTTP clients)
are skipped with a warning. Workers should use `pounce.worker.startup` hook
for per-worker resource initialization.

The lifecycle proof uses exact state sentinels before and after both rolling
reload and health-monitor respawn. State transfer is a JSON-value contract,
not shared object identity: each interpreter receives its own decoded copy.

---

## Graceful Reload

Generational model matching thread-mode behavior:

1. Increment generation.
2. Spawn new subinterpreter workers (fresh app import).
3. Wait for every replacement to report `("serving",)`; on failure, retire the
   replacement and keep the old generation.
4. Close only the old generation's duplicated accept sockets and mark it
   draining.
5. Poll status queues for `("idle",)` responses.
6. Send `("shutdown",)` to old workers.
7. Join old worker threads and retain the replacement handles.

Skip `reimport_app` for subinterpreter mode (each subinterpreter imports fresh).

Timeout handling: if workers don't become idle within `reload_timeout`, a warning
is logged and shutdown is forced.

---

## Known Limitations

1. **App must be importable by path** — local closures and non-importable callables
   don't work. Use `pounce serve --app myapp:app` syntax.
2. **Async workers only** — sync workers (SyncWorker + AsyncPool) can't share
   the AsyncPool across interpreter boundaries.
3. **CPU affinity is per-process** — `sched_setaffinity()` operates on threads,
   not interpreters. All workers share process-level affinity.
4. **C extensions** — some C extensions may not support subinterpreters. If the
   app fails to import, pounce logs the error and the worker dies (auto-restarted
   by health monitor).
5. **Lifespan state is JSON-safe data only** — JSON-serializable values are
   copied into each interpreter. Process-local resources must be initialized
   with `pounce.worker.startup`.
6. **ASGI web-worker scope only** — stability does not extend to the proposed
   job/hybrid worker roles in issue #230. That design must define and prove its
   own state-transfer, crash, reload, and shutdown contract before it can use
   subinterpreters.

---

## Test Coverage

| Test file | Tests | Coverage |
|-----------|-------|---------|
| `tests/unit/test_subinterpreter_worker.py` | Unit proof | IIC protocol, replacement readiness, config serialization, app import, mode detection |
| `tests/unit/test_subinterpreter_drain.py` | Unit proof | Reload acceptor retirement, bounded drain, shutdown command handling |
| `tests/unit/test_config.py` | Unit + property proof | IIC round-trip, edge cases, property-based serialization |
| `tests/integration/test_subinterpreter.py` | Real-interpreter proof | Single/multi worker, exact lifespan state, shutdown under load, stateful respawn, reload under concurrent load, timeout |
| `tests/integration/test_subinterpreter_fd_leak.py` | Real-interpreter proof | Repeated abnormal respawn and forced-reload FD ownership |
| `tests/integration/test_signal_lifecycle.py` | Subprocess proof | SIGHUP reload and SIGTERM lifecycle behavior |
| `benchmarks/worker_modes.py` | Benchmark script | Thread vs subinterpreter comparison |
