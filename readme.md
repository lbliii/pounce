# pounce

A free-threading-native ASGI server for Python 3.14t.

```python
import pounce

pounce.run("myapp:app")
```

Pounce serves ASGI applications using real OS threads sharing a single interpreter — no
fork, no GIL, no per-process memory duplication. On Python 3.14t (free-threading), N worker
threads run N asyncio event loops in parallel, all sharing immutable config and route tables
with zero synchronization overhead.

**Status:** Phase 2 complete — multi-worker ASGI server with automatic GIL detection
(threads on 3.14t, processes on GIL builds), supervisor with health monitoring and crash
restart, connection backpressure, `SO_REUSEPORT` kernel-level load balancing, plus all
Phase 1 features (HTTP/1.1, zstd/gzip compression, Server-Timing, streaming-first pipeline,
ASGI lifespan, CLI). 253 tests passing. See [ROADMAP.md](ROADMAP.md) for the full vision.

## Key Ideas

- **Free-threading first.** Threads, not processes. One interpreter, N event loops, shared
  immutable state. On GIL builds, falls back to multi-process automatically.
- **Pure Python.** No Rust, no C extensions in the server itself. Debuggable, hackable,
  readable. Uses `h11` for HTTP parsing because that problem is solved.
- **Typed end-to-end.** Frozen config, typed ASGI definitions, zero `type: ignore` comments.
  `ty` passes clean.
- **One dependency.** `h11` for HTTP/1.1 parsing. HTTP/2 (`h2`) and WebSocket (`wsproto`)
  are optional extras.
- **Chirp companion.** Built to serve chirp apps natively, but works with any ASGI framework.

## Requirements

- Python >= 3.14

## Usage

### Programmatic

```python
import pounce

# Simple
pounce.run("myapp:app")

# With configuration
pounce.run(
    "myapp:app",
    host="0.0.0.0",
    port=8000,
    workers=4,
)
```

### Command Line

```bash
pounce myapp:app
pounce myapp:app --host 0.0.0.0 --port 8000 --workers 4
```

## How It Works

```
                    ┌─────────────────────────────┐
                    │       Supervisor             │
                    │  detect nogil → threads      │
                    │  detect GIL   → processes    │
                    └──────────────┬──────────────┘
                                   │ spawn N workers
                 ┌─────────────────┼─────────────────┐
                 ▼                 ▼                 ▼
          ┌────────────┐   ┌────────────┐   ┌────────────┐
          │  Worker 1   │   │  Worker 2   │   │  Worker N   │
          │  asyncio    │   │  asyncio    │   │  asyncio    │
          │  event loop │   │  event loop │   │  event loop │
          └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
                │                │                │
                └────────────────┼────────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  Shared Immutable State  │
                    │  (config, app reference) │
                    └─────────────────────────┘
```

On **Python 3.14t** (free-threading): workers are threads. One process, N threads, each with
its own asyncio event loop. Shared memory, no fork overhead, no IPC.

On **GIL builds**: workers are processes. Same API, same config. The supervisor detects the
runtime and adapts.

## Part of the Bengal Ecosystem

```
pounce      ASGI server       (serves apps)
chirp       Web framework     (serves HTML)
kida        Template engine   (renders HTML)
patitas     Markdown parser   (parses content)
rosettes    Syntax highlighter (highlights code)
bengal      Static site gen   (builds sites)
```

## License

MIT
