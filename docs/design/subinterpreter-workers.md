# Design: Subinterpreter Workers (PEP 734)

**Status**: Implemented (Sprints 0-6 complete)
**Date**: 2026-04-10
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
are skipped with a debug log. Workers should use `pounce.worker.startup` hook
for per-worker resource initialization.

---

## Graceful Reload

Generational model matching thread-mode behavior:

1. Increment generation
2. Spawn new subinterpreter workers (fresh app import)
3. Send `("drain",)` to old workers via IIC
4. Poll status queues for `("idle",)` responses
5. Send `("shutdown",)` to old workers
6. Join old worker threads, replace handles

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

---

## Test Coverage

| Test file | Tests | Coverage |
|-----------|-------|---------|
| `tests/unit/test_subinterpreter_worker.py` | 35 unit tests | IIC protocol, config serialization, app import, mode detection |
| `tests/unit/test_config.py` | 7 IIC serialization tests + Hypothesis | Round-trip, edge cases, property-based |
| `tests/integration/test_subinterpreter.py` | 9 integration tests | Single/multi worker, factory app, lifespan state, shutdown under load, respawn, graceful reload, timeout |
| `benchmarks/worker_modes.py` | Benchmark script | Thread vs subinterpreter comparison |
