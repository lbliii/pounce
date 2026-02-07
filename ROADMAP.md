# Pounce

**A free-threading-native ASGI server for Python 3.14t.**

Pounce is an ASGI server designed from scratch for Python's free-threading mode. It uses
real OS threads sharing a single interpreter for parallel request handling — no fork model,
no GIL contention, no per-process memory duplication.

Named after the Bengal cat's hunting instinct, pounce is part of a family of Python tools:
**bengal** (static site generator), **chirp** (web framework), **kida** (template engine),
**patitas** (markdown parser), and **rosettes** (syntax highlighter).

---

## Why Pounce Exists

Every ASGI server was built for a Python that had a GIL:

- **Uvicorn** runs one event loop per process. Parallelism means fork. Four workers means
  four copies of your app, your routes, your templates, your config — all in separate memory.
- **Granian** does its I/O in Rust and calls into Python for the ASGI app. It supports nogil
  threads, but the core server logic isn't Python.
- **Hypercorn** and **Daphne** are process-based. No free-threading awareness.

Python 3.14t changes the game. Without the GIL, threads execute in true parallel. A server
can run four worker threads sharing one interpreter, one copy of the application, one set
of frozen route tables and config — all with zero synchronization for immutable data.

Pounce is built for this world. Pure Python, thread-based, shared-memory, and minimal.

---

## Design Principles

These follow directly from the Bengal ecosystem — the same instincts that shaped chirp,
kida, patitas, and rosettes.

### 1. The obvious thing should be the easy thing

`pounce.run("myapp:app")`. One call, sane defaults, it works. Configuration is optional.
The server detects nogil vs GIL and picks the right worker model automatically. You don't
need to understand the architecture to use it.

### 2. Data should be honest about what it is

`ServerConfig` is frozen because it doesn't change after startup. The ASGI app reference is
assigned once and shared. Per-worker state (connections, metrics) lives on the worker, not
in shared memory. Each piece of data is exactly as mutable as it needs to be.

### 3. Extension should be structural, not ceremonial

Protocol handlers (H1, H2, WS) follow the same interface without inheriting from a base
class. Workers implement the same lifecycle whether they're threads or processes. The
supervisor doesn't care what kind of worker it manages — it cares about the shape.

### 4. The system should be transparent

A request arrives on a socket, gets parsed by h11, produces an ASGI scope, is dispatched
to the app, and the response flows back through h11 to the socket. No hidden middleware,
no implicit transformations, no proxy objects. The data flow is traceable from first byte
to last byte.

### 5. Own what matters, delegate what doesn't

Own the threading model — that's the reason pounce exists. Own the connection lifecycle,
the worker supervision, the ASGI bridge. Delegate HTTP parsing to h11 because that problem
is solved. Delegate TLS to the stdlib or truststore. Never rewrite what doesn't need
rewriting.

---

## Architecture

### Module Layout

```
pounce/
├── __init__.py              # Public API: run(), ServerConfig
├── py.typed                 # PEP 561
│
├── _types.py                # ASGI type definitions (Scope, Receive, Send)
├── config.py                # ServerConfig — frozen dataclass
│
├── server.py                # Server — bind, start supervisor, run
├── supervisor.py            # Supervisor — spawn workers, handle signals
├── worker.py                # Worker — asyncio event loop, accept, dispatch
│
├── protocols/
│   ├── h1.py                # HTTP/1.1 via h11 (phase 1)
│   ├── h2.py                # HTTP/2 via h2 (phase 3)
│   └── ws.py                # WebSocket via wsproto (phase 3)
│
├── asgi/
│   ├── bridge.py            # ASGI scope/receive/send construction
│   └── lifespan.py          # ASGI lifespan protocol
│
├── net/
│   ├── listener.py          # Socket bind, SO_REUSEPORT, accept
│   └── tls.py               # TLS context (phase 3)
│
├── logging.py               # Access log + error log
└── _cli.py                  # CLI entry point (argparse)
```

### Core Abstractions

```
┌───────────────────────────────────────────────────────┐
│  Interface Layer — What users touch                   │
│                                                       │
│  pounce.run()       ServerConfig       CLI            │
└──────────────────────┬────────────────────────────────┘
                       │
┌──────────────────────▼────────────────────────────────┐
│  Supervision Layer — Worker lifecycle                 │
│                                                       │
│  Supervisor          GIL detection     Signal handler │
└──────────────────────┬────────────────────────────────┘
                       │
┌──────────────────────▼────────────────────────────────┐
│  Worker Layer — Event loops and connections            │
│                                                       │
│  Worker (asyncio loop)    Listener (socket accept)    │
└──────────────────────┬────────────────────────────────┘
                       │
┌──────────────────────▼────────────────────────────────┐
│  Protocol Layer — Bytes ↔ ASGI translation             │
│                                                       │
│  H1Protocol (h11)   H2Protocol (h2)   WSProtocol (ws) │
│  ASGI Bridge        Lifespan handler                  │
└───────────────────────────────────────────────────────┘
```

---

## The Server

One call to start, adaptive worker model, graceful shutdown.

```python
import pounce

# Minimal — detects nogil, picks workers automatically
pounce.run("myapp:app")

# Configured
pounce.run(
    "myapp:app",
    host="0.0.0.0",
    port=8000,
    workers=4,
    access_log=True,
)
```

Or from the command line:

```bash
pounce myapp:app --host 0.0.0.0 --port 8000 --workers 4
```

### The Worker Model

On Python 3.14t (free-threading), pounce spawns worker **threads**:

```
┌─────────────────────────────────────────────┐
│               Single Process                │
│                                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐    │
│  │Thread 1  │  │Thread 2  │  │Thread N  │    │
│  │asyncio   │  │asyncio   │  │asyncio   │    │
│  │loop      │  │loop      │  │loop      │    │
│  └─────────┘  └─────────┘  └─────────┘    │
│                                             │
│  Shared: config, app, templates, routes     │
│  Per-worker: connections, event loop        │
└─────────────────────────────────────────────┘
```

On GIL-enabled Python, pounce falls back to worker **processes**:

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Process 1 │  │ Process 2 │  │ Process N │
│ asyncio   │  │ asyncio   │  │ asyncio   │
│ loop      │  │ loop      │  │ loop      │
│ own app   │  │ own app   │  │ own app   │
│ own config│  │ own config│  │ own config│
└──────────┘  └──────────┘  └──────────┘
```

Detection is automatic via `sys._is_gil_enabled()`. Users don't choose.

---

## HTTP Parsing: h11

Pounce uses h11 for HTTP/1.1 parsing — a pure-Python, sans-I/O HTTP implementation. h11
manages a state machine and produces/consumes events without performing any I/O itself.

```python
# Sans-I/O: feed bytes in, get events out
conn = h11.Connection(h11.SERVER)
conn.receive_data(raw_bytes)
event = conn.next_event()  # h11.Request, h11.Data, h11.EndOfMessage
```

Why h11 over httptools:
- Pure Python: no compilation, no C toolchain, debuggable
- Sans-I/O: testable without sockets
- Well-tested: used by Uvicorn, Hypercorn, and many other servers
- Consistent with ecosystem philosophy (patitas, rosettes, kida are all pure Python)

Phase 4 adds httptools as an optional drop-in for users who want C-accelerated parsing.

---

## ASGI Bridge

The bridge translates between pounce's protocol layer and the ASGI 3.0 protocol:

1. h11 parses the request → pounce constructs an ASGI scope dict
2. pounce creates `receive()` and `send()` callables
3. pounce calls `app(scope, receive, send)`
4. The app reads the body via `receive()`, sends the response via `send()`
5. `send()` calls serialize through h11 back to the socket

The bridge is per-request, created and destroyed within a single connection handler. No
shared mutable state.

---

## Free-Threading: By Architecture

Pounce doesn't "pass tests on 3.14t." It makes data races structurally impossible.

- **ServerConfig** is `@dataclass(frozen=True, slots=True)` — shared freely
- **ASGI app reference** is assigned once at startup — never mutated
- **Protocol state** is per-connection — never shared across workers
- **Worker connections** are per-worker sets — never accessed by other workers
- **Shutdown coordination** uses `threading.Event` — write-once, read-many
- **`_Py_mod_gil = 0`** declared in `__init__.py`

The only locks in the system are:
- stdlib `logging` handlers (thread-safe by default)
- Shutdown event (a single `threading.Event`)

Everything else is either immutable or per-worker.

---

## Dependencies

### Core (one dependency)

```
pounce (the server)
└── h11   # HTTP/1.1 parser — pure Python, sans-I/O, well-tested
```

### Optional (explicit extras)

```
pip install pounce[h2]       # h2 — HTTP/2 protocol support
pip install pounce[ws]       # wsproto — WebSocket support
pip install pounce[tls]      # truststore — TLS termination
pip install pounce[full]     # All of the above
```

### Excluded

| Dependency | Reason |
|------------|--------|
| uvloop | C extension; pounce proves pure Python is enough |
| httptools | C binding; h11 is debuggable (httptools available in phase 4) |
| anyio | Not needed; server uses asyncio directly |
| click | CLI uses stdlib argparse |

---

## Phased Roadmap

### Phase 1: It Runs

The minimal viable server. One worker, HTTP/1.1, ASGI compliance.

- [ ] `ServerConfig` frozen dataclass with sane defaults
- [ ] Socket bind and accept via asyncio
- [ ] HTTP/1.1 parsing via h11
- [ ] ASGI bridge (scope, receive, send)
- [ ] ASGI lifespan protocol (startup/shutdown)
- [ ] `pounce.run("app:app")` programmatic API
- [ ] Access logging (method, path, status, timing)
- [ ] Graceful shutdown on SIGINT/SIGTERM
- [ ] Error responses (400 for malformed, 500 for server errors)
- [ ] Request size limits (headers and body)
- [ ] CLI: `pounce myapp:app --host --port --log-level`

### Phase 2: It Scales

Multi-worker mode with automatic GIL detection.

- [ ] Supervisor: spawn N workers, monitor health, restart on crash
- [ ] Thread-based workers on 3.14t (nogil detection)
- [ ] Process-based workers on GIL builds (fallback)
- [ ] `SO_REUSEPORT` for kernel-level load balancing (Linux)
- [ ] Shared socket fallback for macOS
- [ ] Worker count auto-detection from `os.cpu_count()`
- [ ] Connection-level backpressure (per-worker connection limits)
- [ ] Benchmark suite: single-worker and multi-worker throughput

### Phase 3: It's Complete

Full protocol support — HTTP/2, WebSocket, TLS.

- [ ] WebSocket upgrade and lifecycle via wsproto (optional dep)
- [ ] HTTP/2 via h2 library (optional dep)
- [ ] ALPN negotiation for automatic H1/H2 selection
- [ ] TLS termination via stdlib ssl + optional truststore
- [ ] Keep-alive tuning and connection limits
- [ ] CLI: `--reload` for development file watching
- [ ] App factory support: `pounce "myapp:create_app()"`

### Phase 4: It's Fast

Performance optimization pass.

- [ ] Benchmark suite vs Uvicorn and Granian (reproducible, automated)
- [ ] Hot-path profiling with `py-spy` and `perf`
- [ ] Optional httptools backend (`pounce[fast]`) for C-accelerated parsing
- [ ] Zero-copy response paths where possible
- [ ] Connection pooling optimizations
- [ ] Memory profiling: thread workers vs process workers

---

## Non-Goals

Pounce deliberately does not:

- **Include application logic.** No routing, no middleware, no templates, no static files.
  That's chirp's job. Pounce serves ASGI apps.
- **Include HTTP/3.** QUIC is UDP-based and architecturally different. Maybe someday.
- **Include a process manager.** Pounce manages its own workers but doesn't replace systemd
  or container orchestration.
- **Support Python < 3.14.** Free-threading is the reason pounce exists.
- **Support WSGI.** ASGI only. WSGI apps can use an ASGI adapter if needed.
- **Vendor dependencies.** h11 is a dependency, not a vendored copy.
- **Replace Nginx.** Pounce is an application server, not a reverse proxy. Use Nginx or
  Caddy in front of pounce for production routing, caching, and rate limiting.
- **Be a framework.** Pounce has no opinions about your application structure. It takes an
  ASGI callable and serves it.

---

## The Stack

Pounce completes the Bengal ecosystem's server story:

```
pounce      ASGI server       (serves apps)
chirp       Web framework     (serves HTML)
kida        Template engine   (renders HTML)
patitas     Markdown parser   (parses content)
rosettes    Syntax highlighter (highlights code)
bengal      Static site gen   (builds sites)
```

Each tool is independent. Together they form a complete web platform, built for Python 3.14t,
with minimal external dependencies at every layer.

---

*Pounce: because bengals don't wait.*
