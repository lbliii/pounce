# Architecture Design Document: Pounce

**Version**: 0.3.0-dev
**Date**: 2026-02-07
**Status**: Phase 3 implemented

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

6. **Streaming-first.** The response pipeline is designed for chunked streaming as the
   primary path, not buffered-then-send. SSE, htmx streaming, AI token streaming are
   first-class patterns, not edge cases. The dominant response patterns of 2026 — chunked
   HTML (htmx 4.0), event streams (SSE), token delivery (LLM APIs) — are all streaming.

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
    │  │  │  TLS (opt)   │  │  │
    │  │  │  + ALPN      │  │  │
    │  │  └──────┬──────┘  │  │
    │  │         │          │  │
    │  │  ┌──────▼──────┐  │  │
    │  │  │  Protocol    │  │  │
    │  │  │  H1/H2/WS    │  │  │
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
- `ProtocolHandler` — Protocol (structural type) defining the sans-I/O contract
- `H1Protocol` — HTTP/1.1 via h11 (implements `ProtocolHandler`)
- `H2Connection` — HTTP/2 via h2 (optional, sans-I/O wrapper with stream multiplexing)
- `WSProtocol` — WebSocket via wsproto (optional, sans-I/O framing)
- `ASGIBridge` — `bridge.py` (H1), `h2_bridge.py` (H2), `ws_bridge.py` (WS)

**Constraints:**
- Sans-I/O design: protocol handlers process bytes, produce bytes
- No direct socket access — the worker feeds bytes in, reads bytes out
- No asyncio imports — protocol logic is sync and testable

#### 3.4a Protocol Contract

All protocol handlers conform to the same structural interface. No base class —
`Protocol` from `typing` enforces the shape at type-check time.

```python
class ProtocolHandler(Protocol):
    """Sans-I/O contract for all wire protocols."""

    def receive_data(self, data: bytes) -> list[ProtocolEvent]: ...
    def send_response(self, status: int, headers: list[tuple[bytes, bytes]]) -> bytes: ...
    def send_body(self, data: bytes, more: bool = False) -> bytes: ...
    def start_new_cycle(self) -> None: ...
```

The worker interacts with any protocol handler through this interface. H1, H2, and WS
implementations are interchangeable without the worker knowing which protocol is active.

#### 3.4b Protocol Event Types

Protocol handlers produce typed events instead of raw h11 objects. This decouples the
worker from any specific library.

```python
@dataclass(frozen=True, slots=True)
class RequestReceived:
    method: bytes
    target: bytes
    headers: tuple[tuple[bytes, bytes], ...]
    http_version: str

@dataclass(frozen=True, slots=True)
class BodyReceived:
    data: bytes
    more: bool

@dataclass(frozen=True, slots=True)
class ConnectionClosed:
    reason: str

@dataclass(frozen=True, slots=True)
class Upgraded:
    protocol: str  # "websocket", "h2c"

type ProtocolEvent = (
    RequestReceived | BodyReceived | ConnectionClosed | Upgraded
    | WebSocketConnected | WebSocketDataReceived | WebSocketDisconnected
)
```

#### 3.4c Connection Abstraction

Each accepted connection is represented by an immutable metadata record and its
associated protocol handler.

```python
@dataclass(slots=True)
class Connection:
    transport: asyncio.Transport
    protocol: ProtocolHandler
    client: tuple[str, int]
    server: tuple[str, int]
    created_at: float  # time.monotonic()
```

Connections are per-worker and never shared across workers or threads.

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

### 4.2b Connection Flow (HTTP/2)

```
    Client connects (TCP + TLS)
           │
           ▼
    ┌──────────────┐
    │  TLS + ALPN   │  ALPN negotiates "h2"
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  H2Connection │  h2 state machine: preface → streams
    │  (h2 lib)     │  Multiplexed streams on single connection
    └──────┬───────┘
           │ for each stream:
           ▼
    ┌──────────────┐
    │  ASGI Bridge  │  build_h2_scope() per stream
    │  (h2_bridge)  │  Concurrent stream tasks
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  ASGI App     │  app(scope, receive, send) per stream
    └──────────────┘
```

### 4.2c Connection Flow (WebSocket over HTTP/1.1)

```
    Client sends HTTP/1.1 upgrade request
           │
           ▼
    ┌──────────────┐
    │  H1Protocol   │  Detects Connection: Upgrade + Upgrade: websocket
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  101 Response │  Manual HTTP 101 Switching Protocols response
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  WSProtocol   │  wsproto framing (binary/text/close/ping/pong)
    │  (wsproto)    │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  ASGI Bridge  │  build_ws_scope() → websocket ASGI lifecycle
    │  (ws_bridge)  │  connect → accept → send/receive → close
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  ASGI App     │  websocket scope app(scope, receive, send)
    └──────────────┘
```

**Streaming responses** (SSE, chunked HTML, AI token streams) keep the connection open
and send body chunks as the app produces them. The response body is never buffered — each
`send({"type": "http.response.body", "body": chunk, "more_body": True})` call writes
immediately through the protocol layer to the socket. This is the primary response path,
not an edge case.

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
        # Inject Server-Timing header if config.server_timing is True
        ...
    elif message["type"] == "http.response.body":
        # Compress chunk via content-encoding pipeline (if negotiated)
        # Serialize body, write to socket immediately (no buffering)
        # The app controls streaming cadence — pounce writes as fast as
        # the app sends and the client consumes (backpressure via TCP)
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
    """A single worker with its own asyncio event loop.

    Accepts connections and handles them through the full pipeline:
    parse → scope → negotiate compression → ASGI app → response → log.
    """

    def __init__(self, config: ServerConfig, app: ASGIApp, sock: socket.socket) -> None:
        self._config = config           # Shared, frozen
        self._app = app                 # Shared, read-only reference
        self._sock = sock               # Bound, listening socket
        self._shutdown_event = asyncio.Event()
        self._active_connections = 0    # Per-worker, mutable

    def run(self) -> None:
        """Start the event loop and serve forever."""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Accept connections until shutdown is signaled."""
        server = await asyncio.start_server(
            self._handle_connection, sock=self._sock
        )
        await self._shutdown_event.wait()
        server.close()
        await server.wait_closed()
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

### 4.7 Content Encoding Pipeline

Pounce negotiates response compression using Python 3.14's stdlib — no external
dependencies for zstd or gzip.

```
    Client: Accept-Encoding: zstd, gzip;q=0.8
                    │
                    ▼
    ┌───────────────────────────┐
    │   Negotiation (per-req)   │
    │                           │
    │   Parse Accept-Encoding   │
    │   Waterfall:              │
    │   1. zstd  (stdlib)       │
    │   2. gzip  (stdlib)       │
    │   3. identity (no-op)     │
    └─────────┬─────────────────┘
              │
              ▼
    ┌───────────────────────────┐
    │   Compress (per-chunk)    │
    │                           │
    │   Create compressor once  │
    │   Stream-compress each    │
    │   response body chunk     │
    │   Flush on final chunk    │
    └─────────┬─────────────────┘
              │
              ▼
    Set Content-Encoding header
    Remove Content-Length (chunked)
```

**Implementation:**
- `compression.zstd` (PEP 784, Python 3.14 stdlib) — best ratio/speed trade-off, ~76%
  browser support (Chrome 123+, Firefox 126+, Safari 26.0+ partial)
- `zlib` (stdlib) — gzip/deflate, universal fallback
- Brotli intentionally excluded — `brotli`/`brotlicffi` are C extensions that re-enable
  the GIL on Python 3.14t, defeating pounce's free-threading architecture
- Compressor instances are created per-request, never shared across connections or workers
- Streaming responses are compressed chunk-by-chunk, not buffered-then-compressed

**Thread safety:** Each compressor is instantiated per-request inside a single worker's
event loop. No shared state, no locks needed.

### 4.8 Server-Timing

When `config.server_timing = True`, pounce auto-injects a `Server-Timing` header into
every response:

```
Server-Timing: parse;dur=0.3, app;dur=12.1, encode;dur=0.8
```

**Metrics tracked:**
- `parse` — time from first byte received to complete request parsed (h11 events)
- `app` — time spent in `app(scope, receive, send)` (ASGI app execution)
- `encode` — time spent in content-encoding compression (if active)

All timing uses `time.monotonic()` for precision. The timing context is per-request and
lives on the ASGI bridge — no shared mutable state.

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
    """HTTP/1.1 protocol handler wrapping h11.

    Implements the ProtocolHandler contract. Each instance manages a single
    TCP connection through one or more request-response cycles (keep-alive).
    """

    def __init__(self, *, max_incomplete_event_size: int | None = None) -> None:
        kwargs = {}
        if max_incomplete_event_size is not None:
            kwargs["max_incomplete_event_size"] = max_incomplete_event_size
        self._conn = h11.Connection(h11.SERVER, **kwargs)

    def receive_data(self, data: bytes) -> list[ProtocolEvent]:
        """Feed bytes from socket, return typed protocol events."""
        self._conn.receive_data(data)
        events = []
        while True:
            event = self._conn.next_event()
            if event is h11.NEED_DATA or event is h11.PAUSED:
                break
            if isinstance(event, h11.Request):
                events.append(RequestReceived(...))
            elif isinstance(event, h11.Data):
                events.append(BodyReceived(data=event.data, more=True))
            elif isinstance(event, h11.EndOfMessage):
                events.append(BodyReceived(data=b"", more=False))
        return events

    def send_response(self, status: int, headers: list[tuple[bytes, bytes]]) -> bytes:
        """Serialize response start."""
        return self._conn.send(h11.Response(status_code=status, headers=headers))

    def send_body(self, data: bytes, more: bool = False) -> bytes:
        """Serialize response body chunk."""
        parts = []
        if data:
            parts.append(self._conn.send(h11.Data(data=data)))
        if not more:
            parts.append(self._conn.send(h11.EndOfMessage()))
        return b"".join(parts)

    def start_new_cycle(self) -> None:
        """Reset for next request on keep-alive."""
        self._conn.start_next_cycle()
```

### 6.3 Protocol Negotiation (Phase 3 — Implemented)

HTTP/2 (`h2`) and WebSocket (`wsproto`) follow the same sans-I/O pattern and are
integrated as optional protocol handlers without changing the worker's core structure.

**ALPN-based negotiation (TLS connections):**

```python
# Worker._handle_connection — ALPN check for HTTP/2
ssl_obj = writer.get_extra_info("ssl_object")
if ssl_obj and ssl_obj.selected_alpn_protocol() == "h2":
    await self._handle_h2_connection(reader, writer)
    return
```

**Header-based negotiation (HTTP/1.1 connections):**

```python
# Worker._handle_connection — WebSocket upgrade detection
if _is_websocket_upgrade(request):
    await self._handle_websocket(reader, writer, request)
    return
# Otherwise: standard HTTP/1.1 keep-alive loop
```

**RFC 8441 — WebSocket over HTTP/2.** Implemented. `H2Connection` enables
`SETTINGS_ENABLE_CONNECT_PROTOCOL` and detects Extended CONNECT requests with
`:protocol = websocket`, emitting `H2WebSocketRequest` events. The worker dispatches
these to `_handle_h2_websocket_stream()` which manages WS framing within the H2 stream.

**RFC 9218 — Extensible Priority Signals.** Implemented. `_priority.py` provides
`parse_priority()` for the `Priority` header (urgency 0-7, incremental boolean) and
`PriorityScheduler` with a min-heap for urgency-based DATA frame scheduling.

**103 Early Hints.** Implemented. The H2 ASGI bridge sends informational headers when
`status == 103` without marking the response as started, allowing multiple early hints
before the final response. The H1 bridge silently skips 103 responses.

**RFC 9842 — Compression Dictionary Transport.** Standardized September 2025. Enables
delta compression using shared dictionaries with Brotli and Zstandard. This is
experimental and would require an ASGI extension. Future exploration only.

---

## 7. Module Dependency Graph

```
    pounce/__init__.py  (public API: run, ServerConfig)
           │
           │  ── Primitives (leaf nodes, no internal deps) ──────────
           │
           ├── pounce/config.py            (no internal deps)
           ├── pounce/_types.py            (no internal deps; ASGI type aliases)
           ├── pounce/_errors.py           (no internal deps; PounceError hierarchy)
           ├── pounce/_timing.py           (no internal deps; monotonic clock, Server-Timing)
           ├── pounce/_importer.py         (no internal deps; "myapp:app" → callable)
           ├── pounce/_priority.py         (no internal deps; RFC 9218 Priority Signals)
           ├── pounce/_reload.py           (no internal deps; file watcher for --reload)
           │
           │  ── Protocol contracts (depends only on primitives) ────
           │
           ├── pounce/protocols/
           │      ├── _base.py            (depends on _errors.py; ProtocolHandler, events)
           │      ├── h1.py               (external: h11; depends on _base.py)
           │      ├── h2.py               (external: h2; depends on _base.py)
           │      └── ws.py               (external: wsproto; depends on _base.py)
           │
           │  ── Compression (depends on config) ───────────────────
           │
           ├── pounce/_compression.py      (depends on config.py; encoding negotiation)
           │
           │  ── Core modules ──────────────────────────────────────
           │
           ├── pounce/_cli.py
           │      ├── pounce/config.py
           │      └── pounce/_importer.py
           │
           ├── pounce/server.py
           │      ├── pounce/config.py
           │      ├── pounce/supervisor.py
           │      ├── pounce/_importer.py
           │      ├── pounce/_reload.py
           │      ├── pounce/net/tls.py
           │      └── pounce/_types.py
           │
           ├── pounce/supervisor.py
           │      ├── pounce/config.py
           │      ├── pounce/worker.py
           │      └── pounce/_types.py
           │
           ├── pounce/worker.py
           │      ├── pounce/config.py
           │      ├── pounce/_errors.py
           │      ├── pounce/_timing.py
           │      ├── pounce/_compression.py
           │      ├── pounce/protocols/h1.py
           │      ├── pounce/protocols/h2.py
           │      ├── pounce/protocols/ws.py
           │      ├── pounce/asgi/bridge.py
           │      ├── pounce/asgi/h2_bridge.py
           │      ├── pounce/asgi/ws_bridge.py
           │      ├── pounce/net/listener.py
           │      └── pounce/logging.py
           │
           │  ── ASGI subsystem ────────────────────────────────────
           │
           ├── pounce/asgi/
           │      ├── bridge.py            (depends on _types.py, _timing.py)
           │      ├── h2_bridge.py         (depends on _types.py; per-stream H2 ASGI)
           │      ├── ws_bridge.py         (depends on _types.py; WebSocket ASGI lifecycle)
           │      └── lifespan.py          (depends on _types.py, _errors.py)
           │
           │  ── Network subsystem ─────────────────────────────────
           │
           ├── pounce/net/
           │      ├── listener.py          (depends on config.py)
           │      └── tls.py              (depends on config.py, _errors.py)
           │
           └── pounce/logging.py           (depends on config.py)
```

**Key constraints:**
- Primitives (`_errors.py`, `_timing.py`, `_importer.py`, `_types.py`, `config.py`) have
  no internal dependencies and form the foundation layer.
- `pounce/protocols/_base.py` defines the `ProtocolHandler` Protocol and event types.
  Concrete protocol handlers (h1, h2, ws) depend on `_base.py` and their external library.
- `_compression.py` depends only on `config.py` and stdlib modules.
- Dependency direction is strictly downward. No circular imports.

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
Pounce v0.3.0 (Python 3.14.0t, free-threading)
├─ Workers: 4 (threads)
├─ Listening: https://0.0.0.0:8000
├─ App: myapp:app
├─ TLS: enabled
├─ Reload: enabled (watching for changes)
├─ Keep-alive timeout: 10.0s
├─ Max requests/connection: 1000
└─ Press Ctrl+C to stop
```

TLS, reload, and keep-alive tuning lines appear only when the respective features are
enabled or set to non-default values.

---

## 10. Testing Strategy

**Current state:** 369 tests passing (Phase 3).

### 10.1 Unit Tests (Protocol Layer)

Sans-I/O protocol handlers are tested by feeding bytes and asserting output:

```python
def test_h1_simple_get():
    proto = H1Protocol()
    raw = b"GET /hello HTTP/1.1\r\nHost: localhost\r\n\r\n"
    events = proto.receive_data(raw)
    assert isinstance(events[0], RequestReceived)
    assert events[0].method == b"GET"
    assert events[0].target == b"/hello"
```

### 10.2 Unit Tests (ASGI Bridge)

Bridge tests verify scope construction, streaming send, and header injection:

```python
async def test_compression_injection():
    proto = H1Protocol()
    proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    transport = FakeTransport()
    compressor = GzipCompressor()
    send = create_send(proto, transport, compressor=compressor)
    await send({"type": "http.response.start", "status": 200, "headers": [...]})
    await send({"type": "http.response.body", "body": b"hello" * 100})
    assert b"content-encoding: gzip" in bytes(transport.data)
```

### 10.3 Integration Tests (Full Stack)

Start a pounce worker in a background thread, send raw HTTP via socket:

```python
def test_get_hello(hello_app):
    worker, sock, thread = start_worker(hello_app)
    addr = sock.getsockname()
    response = send_raw_request(
        addr, b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    )
    assert b"200" in response
    assert b"Hello, World!" in response
    worker.shutdown()
```

### 10.4 Shared Test Fixtures

`tests/conftest.py` provides reusable ASGI apps (with lifespan support) and helpers:

- `hello_app` — returns "Hello, World!"
- `echo_app` — returns method + path
- `streaming_app` — chunked response in 3 parts
- `error_app` — always raises RuntimeError
- `start_worker()` — spin up a worker in a background thread
- `send_raw_request()` — send raw HTTP bytes and capture response

### 10.5 Unit Tests (Phase 3 Additions)

- TLS: context creation, secure defaults, ALPN protocols, missing cert handling, truststore
- WebSocket: `WSProtocol` framing, `build_ws_accept_key`, `build_101_response`, ASGI bridge,
  `_is_websocket_upgrade` header detection (case-insensitive, missing header variants)
- HTTP/2: `H2Connection` init, preface, request/response lifecycle, multiplexed streams,
  stream reset, GOAWAY handling
- Priority Signals: `parse_priority` parsing, `PriorityScheduler` urgency-based ordering
- Dev Reload: `_snapshot`, `detect_changes`, file creation/modification/deletion, exclude
  patterns for `__pycache__`, `.git`, etc.
- Config validation: `keep_alive_timeout > 0`, `max_requests_per_connection >= 0`
- Error hierarchy: `TLSError`, `ReloadError` inheritance and status codes
- Supervisor: `restart_workers()` event clearing, worker joining, respawn logic
- Package exports: all Phase 3 symbols from `protocols`, `asgi`, `net`, `_errors`

### 10.6 Integration Tests (Phase 3 Additions)

- CLI flag parsing: `--ssl-certfile`, `--ssl-keyfile`, `--reload`, `--keep-alive-timeout`,
  `--max-requests-per-connection`

### 10.7 Future: Benchmark Tests (Phase 4)

Reproducible throughput measurements vs Uvicorn and Granian.

---

## 11. Future Exploration

### 11.1 Subinterpreter Workers (PEP 734)

Python 3.14 ships `concurrent.interpreters` in the stdlib (PEP 734). Subinterpreters
offer a third worker model beyond threads and processes:

| Model | Isolation | Memory | Communication |
|-------|-----------|--------|---------------|
| Processes | Strong (separate address space) | High (duplicated) | IPC (pickle, pipes) |
| Threads (nogil) | Weak (shared interpreter) | Low (shared) | Direct (immutable data) |
| Subinterpreters | Medium (separate state, same process) | Medium (shared code, separate data) | Limited types only |

**Advantages over processes:** No fork overhead, no memory duplication of code objects.
**Advantages over threads:** Stronger isolation — bugs in one interpreter can't corrupt
another's state.

**Limitations (as of 3.14):**
- Can only share `str | bytes | int | float | bool | None | tuple | Queue | memoryview`
- Not all PyPI packages support multiple interpreters
- `InterpreterPoolExecutor` uses pickling (slow) vs `call_in_thread` (fast but limited)
- Not available on WebAssembly platforms

**CPU-bound benchmarks show subinterpreters outperform free-threading.** For I/O-heavy
ASGI workloads, free-threading is more appropriate. But subinterpreters could be valuable
for CPU-heavy ASGI apps (image processing, ML inference, heavy computation in request
handlers) where isolation is more important than shared-memory speed.

**Status:** Phase 5 exploration. Wait for ecosystem maturity before building a third
worker model.

### 11.2 WebTransport (HTTP/3)

WebTransport is a bidirectional transport protocol built on HTTP/3 (QUIC). Browser
coverage is ~82% as of 2026 (Chrome, Firefox, Edge — no Safari/iOS). The specification
is still in Editor's Draft status.

WebTransport offers advantages over WebSocket: reduced head-of-line blocking, faster
performance via QUIC, better network transitions, and UDP-like unreliable datagrams. It
requires UDP socket handling, which is architecturally different from pounce's TCP model.

**Status:** Not in initial phases. Revisit when Safari adds support and the spec
stabilizes.

### 11.3 Compression Dictionary Transport (RFC 9842)

Standardized September 2025. Enables delta compression using shared dictionaries with
Brotli and Zstandard. Instead of compressing each resource independently, the server can
reference a previously transmitted resource as a dictionary — sending only the diff.

This would require an ASGI extension for applications to declare available dictionaries
and a server-side cache of dictionary metadata. Experimental and forward-looking.

**Status:** Future ASGI extension exploration.
