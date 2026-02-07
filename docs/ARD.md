# Architecture Design Document: Pounce

**Version**: 0.1.0-draft
**Date**: 2026-02-07
**Status**: Draft

---

## 1. Architectural Goals

1. **Free-threading by construction.** Thread-based parallelism is the default, not an
   afterthought. Immutable shared state, per-thread event loops, no GIL assumptions.

2. **Pure Python, production grade.** No C extensions, no Rust, no FFI in the server core.
   Prove that free-threaded Python can serve HTTP at production-quality throughput.

3. **Typed end-to-end.** Zero `type: ignore` comments. Every internal interface has complete
   type annotations. The type checker (`ty`) is a first-class development tool.

4. **One dependency.** `h11` for HTTP/1.1 parsing. Everything else is optional. The server
   owns its I/O, its lifecycle, and its threading model.

5. **ASGI and nothing more.** Pounce is a server, not a framework. It accepts an ASGI
   callable and serves it. No routing, no middleware, no opinions about application structure.

---

## 2. System Context

```
    ┌────────────────────────┐
    │       Client           │
    │  (browser, curl, etc.) │
    └───────────┬────────────┘
                │ TCP / TLS
    ┌───────────▼────────────┐
    │       Pounce           │
    │                        │
    │  ┌──────────────────┐  │
    │  │   Supervisor      │  │
    │  │  (lifecycle mgmt) │  │
    │  └────────┬─────────┘  │
    │           │ spawn       │
    │  ┌────────▼─────────┐  │
    │  │   Worker Thread   │  │
    │  │   (asyncio loop)  │  │
    │  │                   │  │
    │  │  ┌─────────────┐  │  │
    │  │  │  Listener    │  │  │
    │  │  │  (accept)    │  │  │
    │  │  └──────┬──────┘  │  │
    │  │         │          │  │
    │  │  ┌──────▼──────┐  │  │
    │  │  │  H1 Protocol │  │  │
    │  │  │  (h11)       │  │  │
    │  │  └──────┬──────┘  │  │
    │  │         │          │  │
    │  │  ┌──────▼──────┐  │  │
    │  │  │  ASGI Bridge │  │  │
    │  │  │  (scope/     │  │  │
    │  │  │   recv/send) │  │  │
    │  │  └──────┬──────┘  │  │
    │  │         │          │  │
    │  └─────────│─────────┘  │
    │            │             │
    └────────────│─────────────┘
                 │ ASGI protocol
    ┌────────────▼─────────────┐
    │     ASGI Application     │
    │  (chirp, Starlette, etc) │
    └──────────────────────────┘
```

---

## 3. Layer Architecture

Pounce is organized into four layers with strict dependency direction: each layer depends
only on layers below it. No upward dependencies. No circular imports.

### 3.1 Interface Layer

**Purpose:** Entry points — CLI and programmatic API.

**Components:**
- `pounce.run()` — programmatic entry point
- `pounce._cli` — argparse-based CLI (`pounce myapp:app`)
- `ServerConfig` — frozen configuration dataclass

**Constraints:**
- Translates user input into `ServerConfig` and an app reference
- No server logic in this layer

### 3.2 Supervision Layer

**Purpose:** Worker lifecycle management. Spawning, monitoring, restarting, shutdown.

**Components:**
- `Supervisor` — spawns and manages workers
- GIL detection — `sys._is_gil_enabled()` to choose threads vs processes

**Constraints:**
- Knows about workers but not about HTTP or ASGI
- Handles signals (SIGINT, SIGTERM)
- Restarts crashed workers

### 3.3 Worker Layer

**Purpose:** The core event loop. Each worker owns one asyncio event loop and handles
connections from accept to close.

**Components:**
- `Worker` — runs an asyncio event loop, accepts connections
- `Listener` — socket bind and accept

**Constraints:**
- One event loop per worker (asyncio is single-threaded)
- Workers share immutable state (config, app reference)
- Workers own mutable state (connections, per-worker metrics)

### 3.4 Protocol Layer

**Purpose:** Translates bytes on the wire into ASGI messages and back.

**Components:**
- `H1Protocol` — HTTP/1.1 via h11
- `H2Protocol` — HTTP/2 via h2 (phase 3, optional)
- `WSProtocol` — WebSocket via wsproto (phase 3, optional)
- `ASGIBridge` — constructs scope, receive, send for the application

**Constraints:**
- Sans-I/O design: protocol handlers process bytes, produce bytes
- No direct socket access — the worker feeds bytes in, reads bytes out
- No asyncio imports — protocol logic is sync and testable

---

## 4. Component Design

### 4.1 Server Lifecycle

```
    ┌──────────┐     ┌───────────┐     ┌──────────┐     ┌───────────┐
    │  CONFIG   │────>│  BIND     │────>│  SERVE   │────>│  SHUTDOWN │
    └──────────┘     └───────────┘     └──────────┘     └───────────┘

    Parse args        Bind socket(s)    Workers handle    Drain conns
    Load app          Verify address    requests          Stop workers
    Create config     Set SO_REUSEPORT  Supervisor        Close sockets
                                        monitors
```

**Config phase:** Parse CLI arguments or programmatic config. Import the ASGI app. Create
a frozen `ServerConfig`. After this phase, all configuration is immutable.

**Bind phase:** Create and bind server socket(s). On Linux with `SO_REUSEPORT`, each worker
can bind independently. On macOS, the supervisor binds once and workers share the socket.

**Serve phase:** The supervisor spawns workers. Each worker runs its own asyncio event loop,
accepting connections and dispatching them through the protocol layer to the ASGI app.

**Shutdown phase:** On SIGINT/SIGTERM, the supervisor signals all workers to stop accepting
new connections, waits for in-flight requests to complete (up to `shutdown_timeout`), then
exits.

### 4.2 Connection Flow (HTTP/1.1)

```
    Client connects (TCP)
           │
           ▼
    ┌──────────────┐
    │  Worker       │  asyncio event loop picks up new connection
    │  (accept)     │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  H1Protocol   │  h11 state machine: IDLE → SEND_RESPONSE
    │  (parse)      │  Reads bytes → produces h11 events
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  ASGI Bridge  │  Construct scope dict from h11 request
    │  (translate)  │  Create receive() → feeds request body
    │               │  Create send() → captures response events
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  ASGI App     │  app(scope, receive, send)
    │  (execute)    │  App reads body via receive()
    │               │  App sends response via send()
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  H1Protocol   │  send() calls → h11 response bytes
    │  (serialize)  │  Write to socket
    └──────┬───────┘
           │
           ▼
    Keep-alive? → loop back to parse
    Close? → close socket
```

### 4.3 ASGI Bridge

The bridge translates between pounce's protocol layer and the ASGI 3.0 spec:

**Scope construction:**

```python
scope = {
    "type": "http",
    "asgi": {"version": "3.0", "spec_version": "2.4"},
    "http_version": "1.1",
    "method": request.method,
    "path": request.target,
    "raw_path": request.raw_target,
    "query_string": query_bytes,
    "root_path": config.root_path,
    "headers": [(name, value), ...],  # Raw bytes
    "server": (config.host, config.port),
    "client": (remote_host, remote_port),
}
```

**receive()** — Returns request body chunks:

```python
async def receive() -> dict[str, Any]:
    chunk = await read_body_chunk()
    return {
        "type": "http.request",
        "body": chunk,
        "more_body": has_more,
    }
```

**send()** — Accepts response start and body:

```python
async def send(message: dict[str, Any]) -> None:
    if message["type"] == "http.response.start":
        # Buffer status + headers, serialize via h11
        ...
    elif message["type"] == "http.response.body":
        # Serialize body, write to socket
        ...
```

### 4.4 Supervisor Design

```
    ┌─────────────────────────────────────┐
    │            Supervisor               │
    │                                     │
    │  ┌─────────────┐                    │
    │  │ GIL Detect   │                    │
    │  │ sys._is_gil_ │──┐                │
    │  │ enabled()    │  │                 │
    │  └─────────────┘  │                 │
    │                    │                 │
    │       ┌────────────▼──────────┐      │
    │       │                       │      │
    │  ┌────▼─────┐          ┌─────▼────┐ │
    │  │ Thread    │          │ Process  │ │
    │  │ Spawner   │          │ Spawner  │ │
    │  │ (nogil)   │          │ (GIL)    │ │
    │  └────┬─────┘          └─────┬────┘ │
    │       │                      │       │
    │       ▼                      ▼       │
    │  Workers share          Workers get  │
    │  interpreter            own process  │
    │  + immutable state      + fork       │
    └─────────────────────────────────────┘
```

The supervisor's job:

1. **Detect runtime**: `sys._is_gil_enabled()` → threads or processes
2. **Spawn workers**: Start N workers with shared config and app
3. **Monitor health**: Watch for worker crashes, restart if needed
4. **Handle signals**: SIGINT/SIGTERM → initiate graceful shutdown
5. **Coordinate shutdown**: Signal workers, wait for drain, exit

### 4.5 Worker Design

Each worker is self-contained:

```python
class Worker:
    """A single worker with its own asyncio event loop."""

    def __init__(self, config: ServerConfig, app: ASGIApp) -> None:
        self._config = config  # Shared, frozen
        self._app = app        # Shared, read-only reference
        self._connections: set[Connection] = set()  # Per-worker, mutable

    def run(self) -> None:
        """Start the event loop and serve forever."""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Accept connections and handle them."""
        server = await asyncio.start_server(
            self._handle_connection,
            sock=self._socket,
        )
        ...
```

Workers own:
- Their asyncio event loop (one per worker, never shared)
- Their set of active connections (mutable, per-worker)
- Per-worker metrics (request count, active connections)

Workers share (read-only):
- `ServerConfig` (frozen dataclass)
- ASGI app reference
- Logging configuration

### 4.6 Backpressure

Backpressure prevents the server from accepting more work than it can handle:

**Connection-level:** If the number of active connections on a worker exceeds a threshold,
the worker pauses accepting new connections until some complete.

**Request-level:** If the ASGI app is slow to consume the request body (slow `receive()`
calls), the protocol handler pauses reading from the socket. h11's sans-I/O design makes
this natural — we simply stop feeding it bytes.

**Response-level:** If the client is slow to consume the response (TCP backpressure), the
asyncio transport's `write()` buffers data. When the buffer exceeds a threshold, we pause
the ASGI app's `send()` calls via flow control.

---

## 5. Thread Safety Architecture

### 5.1 Immutability Categories

| Component | Mutability | Phase | Mechanism |
|-----------|-----------|-------|-----------|
| ServerConfig | Immutable | Always | `@dataclass(frozen=True, slots=True)` |
| ASGI app reference | Immutable | After startup | Assigned once, never changed |
| Log formatters | Immutable | After startup | Created once, shared |
| Worker connections | Mutable | Per-worker | Never shared across workers |
| Worker metrics | Mutable | Per-worker | Never shared across workers |
| Supervisor state | Mutable | Supervisor only | Single-threaded supervisor loop |
| Shutdown signal | Write-once | Shutdown | `threading.Event` |

### 5.2 What Has No Locks

- Config access (frozen dataclass, shared freely)
- App dispatch (`app(scope, receive, send)` — each call is independent)
- Protocol handling (per-connection state, never shared)
- ASGI bridge (per-request, created and destroyed within one connection)
- Scope construction (dict created fresh per request)

### 5.3 What Needs Care

- **Shutdown coordination:** The supervisor sets a `threading.Event`. Workers check it
  periodically or when notified. This is a write-once pattern — no contention.

- **Worker crash detection:** The supervisor joins on worker threads/processes. On thread
  death, `threading.Thread.is_alive()` is checked. This is read-only from the supervisor.

- **Socket sharing (macOS):** If `SO_REUSEPORT` is not available, the supervisor binds
  once and passes the socket file descriptor to workers. The socket itself is thread-safe
  for `accept()` on the same fd.

- **Access log writes:** If multiple workers write to the same log file, writes must be
  coordinated. Use a `logging.Handler` with a lock (stdlib logging is thread-safe) or
  per-worker log files.

---

## 6. Protocol Layer Design

### 6.1 Sans-I/O Pattern

Protocol handlers follow the sans-I/O pattern: they consume bytes and produce bytes
without performing any I/O themselves. The worker feeds data in and reads data out.

```
    Worker (asyncio I/O)          Protocol (sans-I/O)
    ─────────────────────         ──────────────────────
    recv bytes from socket  ──>   feed bytes to h11
                                  h11 produces events
                            <──   return parsed request
    call ASGI app           ──>
    app calls send()        <──   serialize response via h11
    write bytes to socket   <──   return response bytes
```

This separation enables:
- **Testing without sockets:** Feed bytes in, assert bytes out
- **Protocol reuse:** Same protocol handler works with any I/O backend
- **Clear error boundaries:** Parse errors are protocol errors, not I/O errors

### 6.2 h11 Integration

h11 is a sans-I/O HTTP/1.1 implementation. It manages an internal state machine:

```
    IDLE → SEND_RESPONSE → SEND_BODY → DONE → IDLE (keep-alive)
```

Pounce wraps h11 in `H1Protocol`:

```python
class H1Protocol:
    """HTTP/1.1 protocol handler wrapping h11."""

    def __init__(self, config: ServerConfig) -> None:
        self._conn = h11.Connection(h11.SERVER)
        self._config = config

    def receive_data(self, data: bytes) -> list[h11.Event]:
        """Feed bytes from socket, return parsed events."""
        self._conn.receive_data(data)
        events = []
        while True:
            event = self._conn.next_event()
            if event is h11.NEED_DATA or event is h11.PAUSED:
                break
            events.append(event)
        return events

    def send_response(self, status: int, headers: list[tuple[bytes, bytes]]) -> bytes:
        """Serialize response start."""
        return self._conn.send(h11.Response(status_code=status, headers=headers))

    def send_body(self, data: bytes, more: bool = False) -> bytes:
        """Serialize response body chunk."""
        if more:
            return self._conn.send(h11.Data(data=data))
        return self._conn.send(h11.Data(data=data)) + self._conn.send(h11.EndOfMessage())
```

### 6.3 Future Protocols

HTTP/2 (`h2`) and WebSocket (`wsproto`) follow the same sans-I/O pattern. They can be
added as optional protocol handlers without changing the worker or ASGI bridge layers.

```python
# Phase 3: protocol negotiation
if connection.is_h2():
    protocol = H2Protocol(config)
elif connection.is_websocket_upgrade():
    protocol = WSProtocol(config)
else:
    protocol = H1Protocol(config)
```

---

## 7. Module Dependency Graph

```
    pounce/__init__.py  (public API: run, ServerConfig)
           │
           ├── pounce/config.py          (no internal deps)
           │
           ├── pounce/_types.py          (no internal deps; ASGI type aliases)
           │
           ├── pounce/_cli.py
           │      └── pounce/config.py
           │
           ├── pounce/server.py
           │      ├── pounce/config.py
           │      ├── pounce/supervisor.py
           │      └── pounce/_types.py
           │
           ├── pounce/supervisor.py
           │      ├── pounce/config.py
           │      ├── pounce/worker.py
           │      └── pounce/_types.py
           │
           ├── pounce/worker.py
           │      ├── pounce/config.py
           │      ├── pounce/protocols/h1.py
           │      ├── pounce/asgi/bridge.py
           │      ├── pounce/net/listener.py
           │      └── pounce/logging.py
           │
           ├── pounce/protocols/
           │      ├── h1.py              (external: h11; no internal deps)
           │      ├── h2.py              (external: h2; no internal deps)
           │      └── ws.py              (external: wsproto; no internal deps)
           │
           ├── pounce/asgi/
           │      ├── bridge.py          (depends on _types.py)
           │      └── lifespan.py        (depends on _types.py)
           │
           ├── pounce/net/
           │      ├── listener.py        (depends on config.py)
           │      └── tls.py             (depends on config.py)
           │
           └── pounce/logging.py         (depends on config.py)
```

**Key constraint:** `pounce/protocols/` has no internal dependencies. Each protocol handler
depends only on its external library (h11, h2, wsproto) and can be tested in complete
isolation.

---

## 8. Decisions and Trade-offs

### 8.1 Pure Python (No Rust, No C Extensions)

**Decision:** Pounce is pure Python. No Rust I/O layer, no C extensions in the server core.

**Rationale:** The Bengal ecosystem values debuggability, hackability, and minimal build
complexity. Pounce exists to prove that free-threaded Python is fast enough. If we use Rust
for I/O, we're proving Rust is fast — which is already known.

**Trade-off:** Lower ceiling on raw I/O throughput compared to Granian. Mitigated by
offering `httptools` as an optional C-accelerated parser in phase 4.

### 8.2 h11 Over httptools

**Decision:** h11 is the default HTTP parser. httptools is an optional accelerator.

**Rationale:** h11 is pure Python, sans-I/O, well-tested, and debuggable. It follows the
same philosophy as the rest of the ecosystem. httptools is a C binding that's faster but
adds a compilation step and is harder to debug.

**Trade-off:** ~30-40% lower parsing throughput compared to httptools. Acceptable for phase
1. Phase 4 adds httptools as an optional drop-in replacement.

### 8.3 asyncio Only (No Trio, No anyio)

**Decision:** Pounce uses asyncio directly. No anyio abstraction layer.

**Rationale:** A server is infrastructure, not application code. It doesn't need async
library portability. asyncio is the standard library, requires no dependency, and is the
loop that 3.14t's free-threading is designed around.

**Trade-off:** Cannot run on Trio. This is acceptable — the *application* can use anyio
(chirp does), but the server itself doesn't need the abstraction.

### 8.4 Threads on Nogil, Processes on GIL

**Decision:** Workers are threads on 3.14t, processes on GIL builds.

**Rationale:** This is pounce's core value proposition. Threads share memory, eliminating
the O(N) memory overhead of process-based deployments. On GIL builds, threads can't run
Python in parallel, so processes are the correct fallback.

**Trade-off:** Two code paths to maintain (thread spawning vs process spawning). Mitigated
by keeping the worker implementation identical — only the spawning mechanism changes.

### 8.5 One Event Loop Per Worker Thread

**Decision:** Each worker thread runs its own asyncio event loop.

**Rationale:** asyncio event loops are not designed to be shared across threads. Even under
nogil, running multiple threads on one event loop would require extensive locking. One loop
per thread is how Granian does it and how asyncio is designed to work.

**Trade-off:** Connections are affined to the worker that accepted them. No work-stealing
between workers. This is standard and acceptable.

---

## 9. Observability

### 9.1 Access Logging

```
2026-02-07 10:32:15 INFO 127.0.0.1:54321 - "GET /api/users HTTP/1.1" 200 1234 12.3ms
```

Format: `{timestamp} {level} {client} - "{method} {path} {version}" {status} {bytes} {time}`

Access logging is:
- Enabled by default (`config.access_log = True`)
- Written via stdlib `logging` (thread-safe)
- Per-request timing from first byte received to last byte sent

### 9.2 Error Logging

Server errors (connection resets, parse failures, app exceptions) are logged at ERROR level
with full context:
- Remote address
- Request method and path (if available)
- Exception type and message
- Traceback (in debug mode)

### 9.3 Startup Banner

```
Pounce v0.1.0 (Python 3.14.0t, free-threading)
├─ Workers: 4 (threads)
├─ Listening: http://0.0.0.0:8000
├─ App: myapp:app
└─ Press Ctrl+C to stop
```

---

## 10. Testing Strategy

### 10.1 Unit Tests (Protocol Layer)

Sans-I/O protocol handlers are tested by feeding bytes and asserting output:

```python
def test_h1_simple_request():
    proto = H1Protocol(config)
    events = proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert len(events) == 2  # Request + EndOfMessage
    assert events[0].method == b"GET"
```

### 10.2 Integration Tests (Full Stack)

Start a pounce server in a thread, make HTTP requests, assert responses:

```python
async def test_hello_world():
    async with pounce_server(hello_app, port=0) as server:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://localhost:{server.port}/")
            assert response.status_code == 200
            assert response.text == "Hello, World!"
```

### 10.3 Stress Tests

Concurrent load tests to verify thread safety on 3.14t:

```python
@pytest.mark.slow
async def test_concurrent_requests():
    async with pounce_server(hello_app, workers=4, port=0) as server:
        async with httpx.AsyncClient() as client:
            tasks = [client.get(f"http://localhost:{server.port}/") for _ in range(10_000)]
            responses = await asyncio.gather(*tasks)
            assert all(r.status_code == 200 for r in responses)
```

### 10.4 Benchmark Tests

Reproducible throughput measurements:

```python
@pytest.mark.benchmark
def test_throughput_single_worker(benchmark):
    # Measure requests/second for a minimal ASGI app
    ...
```
