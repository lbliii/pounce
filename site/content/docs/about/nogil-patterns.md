---
title: Free-Threading Patterns
description: Ten architectural patterns for building concurrent Python 3.14t applications
weight: 7
---

# Free-Threading Patterns for Python 3.14t

## Lessons from building Pounce

Python 3.14t removes the Global Interpreter Lock, enabling true parallelism across
threads sharing a single interpreter. This document distills the architectural
patterns Pounce uses to exploit free-threading safely and efficiently. These
patterns are not HTTP-specific -- they apply to any concurrent Python infrastructure:
task schedulers, message brokers, data pipelines, game servers.

Each pattern targets one goal: **eliminate shared mutable state, or make the
remaining shared state trivially correct.**

---

### 1. Frozen Configuration as Lock Elimination

**The pattern.** Declare all configuration as a frozen, slotted dataclass. Validate
exhaustively at construction time. Share the single instance across every worker
thread by reference.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1
    keep_alive_timeout: float = 5.0
    max_request_size: int = 1_048_576
    compression: bool = True
    # ... 60+ fields
```

**Why it works.** `frozen=True` makes every attribute read-only after `__init__`.
Multiple threads reading the same frozen object require zero synchronization --
there is no write to race against. `slots=True` eliminates `__dict__`, preventing
accidental monkey-patching at runtime. `kw_only=True` forces explicit construction,
catching misconfiguration at boot rather than under load.

**In Pounce.** `ServerConfig` carries 60+ fields with 93 validations at boot.
Every worker thread holds a reference to the same object. No per-access locking,
no defensive copies, no stale-config bugs.

**Anti-pattern.** A mutable config dict protected by a lock on every read. Under
free-threading, a lock-per-read on the hot path (every request checks `config.keep_alive_timeout`)
introduces contention that scales inversely with core count.

**Generalization.** Any state that is read frequently and written never (or only at
startup) belongs in a frozen dataclass. Feature flags, route tables, TLS contexts,
database connection parameters -- freeze them at boot.

---

### 2. Immutable Events as Thread-Safe Communication

**The pattern.** Model every observable side effect as a frozen dataclass with a
nanosecond monotonic timestamp. Events cross thread boundaries without copying,
serialization, or locking.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectionOpened:
    connection_id: int
    worker_id: int
    client_addr: str
    client_port: int
    server_addr: str
    server_port: int
    protocol: str       # "h1", "h2", "websocket"
    timestamp_ns: int

@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseCompleted:
    connection_id: int
    worker_id: int
    status: int
    bytes_sent: int
    duration_ms: float
    timestamp_ns: int
```

**Why it works.** A frozen dataclass is immutable after creation. The producing
thread creates it; any number of consuming threads can read it concurrently.
`time.monotonic_ns()` provides a high-resolution, monotonic clock that is
immune to NTP adjustments, giving events a total ordering.

**In Pounce.** Five event types (`ConnectionOpened`, `RequestStarted`,
`ResponseCompleted`, `ClientDisconnected`, `ConnectionCompleted`) flow from
worker threads to a `LifecycleCollector`. The `BufferedCollector` accumulates
events under a single lock at the collector boundary -- the events themselves
need no protection.

**Generalization.** Any observer, event bus, or audit log pattern becomes
trivially thread-safe when events are frozen value objects. This applies to
domain event sourcing, distributed tracing spans, and metrics collection.

---

### 3. Sans-I/O Protocol Design

**The pattern.** Protocol handlers are pure state machines. They consume bytes
and produce typed events plus bytes to send. No socket access, no `asyncio`
import, no I/O of any kind.

```python
@runtime_checkable
class ProtocolHandler(Protocol):
    def receive_data(self, data: bytes) -> list[ProtocolEvent]: ...
    def send_response(self, status: int, headers: list[tuple[bytes, bytes]]) -> bytes: ...
    def send_body(self, data: bytes, *, more: bool) -> bytes: ...
    def start_new_cycle(self) -> None: ...
```

The worker feeds raw bytes in, reads parsed events and serialized bytes out:

```python
events = handler.receive_data(raw_bytes)
for event in events:
    match event:
        case RequestReceived(method=method, target=target, headers=headers):
            response_bytes = handler.send_response(200, response_headers)
            body_bytes = handler.send_body(body, more=False)
```

**Why it works.** Each worker thread creates its own protocol handler instance.
No shared state means no contention. The handler is a pure function of its
accumulated input -- deterministic, reproducible, and testable with plain
`pytest` (no event loop, no mock sockets).

**In Pounce.** H1, H2, and WebSocket each implement sans-I/O handlers. The
same handler works under both the sync worker (blocking `recv`/`send`) and
the async worker (`asyncio` streams). The worker is the I/O adapter; the
protocol is the logic.

**Generalization.** Any parser or encoder benefits from this separation:
database wire protocols, message queue framing, serialization codecs. The
sans-I/O pattern is especially powerful under free-threading because it
guarantees thread isolation by construction rather than by discipline.

---

### 4. Queue-Based Thread Handoff

**The pattern.** When work must transfer between specialized threads, use a
typed `queue.Queue` with frozen or slotted handoff objects. No shared mutable
state; just message passing.

```python
@dataclass(slots=True)
class StreamingHandoff:
    conn: socket.socket
    scope: dict[str, Any]
    body: bytes
    request_id: str | None

@dataclass(slots=True)
class WebSocketHandoff:
    conn: socket.socket
    request: RequestReceived
    client: tuple[str, int]
    server: tuple[str, int]
    scope: dict[str, Any]

type HandoffRequest = StreamingHandoff | WebSocketHandoff

# In SyncWorker: enqueue handoff
handoff_queue.put(StreamingHandoff(conn=conn, scope=scope, body=body, request_id=rid))

# In AsyncPool: dequeue and continue
handoff = handoff_queue.get(timeout=0.1)
```

**Why it works.** `queue.Queue` is internally synchronized. The handoff object
captures everything the receiving thread needs -- no back-references to the
sender's state. Ownership transfers cleanly: the sync worker stops touching
the socket after enqueuing.

**In Pounce.** SyncWorkers handle fast request-response cycles in tight
blocking loops. When an ASGI app returns a streaming response or WebSocket
upgrade, the SyncWorker hands the live socket to the AsyncPool via a typed
handoff. The AsyncPool wraps it in `asyncio` streams and continues the ASGI
lifecycle. Two different execution models cooperate without sharing mutable
state.

**Generalization.** Producer-consumer pipelines, work stealing, and
staged-event-driven architectures all reduce to typed queue handoffs. Under
free-threading, this is faster than shared-state-with-locks because the
queue lock is held only for the enqueue/dequeue, not for the entire
processing duration.

---

### 5. Accept Distributor (Thundering Herd Fix)

**The pattern.** A single dedicated thread calls `accept()` on the listening
socket and enqueues connections into a shared `Queue`. Worker threads pull
from the queue -- the first idle worker wins.

```python
class AcceptDistributor:
    def __init__(self, sock, conn_queue, *, shutdown_event=None, ssl_context=None):
        self._sock = sock
        self._conn_queue = conn_queue
        self._ext_shutdown = shutdown_event

    def run(self):
        while not (self._ext_shutdown and self._ext_shutdown.is_set()):
            self._sock.settimeout(0.25)
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._conn_queue.put((conn, addr))
```

**Why it works.** Without `SO_REUSEPORT` (unavailable on macOS and Windows),
multiple threads blocking on `accept()` on the same fd causes a thundering
herd -- the kernel wakes all threads, but only one gets the connection.
A single accept thread eliminates this entirely. The `Queue` provides
natural load balancing: idle workers dequeue first.

**In Pounce.** The supervisor detects whether workers share the same socket
(no `SO_REUSEPORT`) and starts an `AcceptDistributor` thread automatically.
On Linux with `SO_REUSEPORT`, each worker gets its own socket and accepts
directly.

**Generalization.** Any multi-consumer socket pattern on platforms without
kernel-level load balancing benefits from this. It also applies to file
descriptor distribution in database connection pools and task queue brokers.

---

### 6. The Brotli Principle: C Extensions Are the Enemy

**The pattern.** Audit every dependency for GIL re-acquisition. A single C
extension that takes the GIL under free-threading collapses your parallelism
back to serial execution.

```python
# compression.py -- Pounce's encoding priority
# zstd: stdlib (PEP 784), GIL-free on 3.14t
# gzip: stdlib zlib, GIL-free on 3.14t
# brotli: EXCLUDED -- C extension re-enables GIL

try:
    from compression import zstd as _zstd
    _HAS_ZSTD = True
except ImportError:
    _HAS_ZSTD = False

_ENCODING_PRIORITY: Final[tuple[str, ...]] = _build_encoding_priority()
# Result: ("zstd", "gzip") -- never "br"
```

**Why it matters.** On CPython 3.14t, C extensions that have not been updated
for free-threading will re-enable the GIL for the entire process when imported.
This is silent and catastrophic: your `sys._is_gil_enabled()` check at startup
returns `True`, and all your threading gains vanish.

**In Pounce.** Brotli is intentionally excluded despite being the most popular
web compression format. The `brotli` C extension re-enables the GIL on 3.14t.
Pounce prefers `zstd` (stdlib in 3.14 via PEP 784) and `gzip` (stdlib `zlib`),
both of which are GIL-free.

**The audit checklist:**
1. Run `python -X gil=0 -c "import your_dep; print(sys._is_gil_enabled())"` for every dependency.
2. If it prints `True`, that dependency re-enables the GIL.
3. Find a pure Python alternative, a stdlib replacement, or vendor a GIL-free fork.
4. Add a CI check that asserts `sys._is_gil_enabled() == False` after all imports.

**Generalization.** This is the most important pattern in this document. A
single careless `import` can silently negate your entire free-threading
architecture. Treat GIL-reacquiring C extensions as you would a security
vulnerability: audit, detect, and eliminate.

---

### 7. Functional State Machine (Elm Architecture)

**The pattern.** Model lifecycle transitions as an immutable state plus a pure
reducer function. Dispatch actions to advance state. Render views from state.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ServerModel:
    phase: Phase = Phase.INIT
    effective_workers: int = 0
    mode_label: str = ""
    gil_status: str = ""
    generation: int = 0

def server_reducer(state: ServerModel | None, action: Action) -> ServerModel:
    if state is None:
        state = ServerModel()

    match action.type:
        case "BANNER":
            return replace(state, phase=Phase.STARTUP,
                           effective_workers=action.payload["effective_workers"],
                           mode_label=action.payload["mode_label"],
                           gil_status=action.payload["gil_status"])
        case "READY":
            return replace(state, phase=Phase.READY)
        case "SHUTDOWN_START":
            return replace(state, phase=Phase.SHUTTING_DOWN,
                           connections=action.payload.get("connections", 0))
        case "RELOAD_COMPLETE":
            p = action.payload or {}
            return replace(state, phase=Phase.SERVING,
                           generation=p.get("generation", state.generation))
        case _:
            return state
```

**Why it works.** `replace()` on a frozen dataclass returns a new instance --
the old state is never mutated. The reducer is a pure function: same input
always produces same output. This makes lifecycle transitions deterministic,
testable (call the reducer directly in unit tests), and safe to invoke from
any thread.

**In Pounce.** The server lifecycle flows through `Phase.INIT -> STARTUP ->
READY -> SERVING -> SHUTTING_DOWN -> STOPPED`. Actions like `BANNER`, `READY`,
`SHUTDOWN_START`, and `RELOAD_COMPLETE` drive transitions. A render middleware
produces branded terminal output on each dispatch.

**Generalization.** Any workflow or state machine benefits: deployment
pipelines, connection pool states, circuit breakers, retry policies. The Elm
Architecture makes concurrent state transitions trivially correct because
there is no mutable state to corrupt.

---

### 8. Per-Request Fresh Instances

**The pattern.** Create a fresh compressor, parser, or handler for each request.
Never pool. Never share.

```python
class Compressor(Protocol):
    def compress(self, data: bytes) -> bytes: ...
    def flush(self) -> bytes: ...

def create_compressor(encoding: str, config: ServerConfig) -> Compressor:
    match encoding:
        case "zstd":
            return ZstdCompressor(level=config.compression_level)
        case "gzip":
            return GzipCompressor(level=config.compression_level)
```

Each request gets its own compressor:

```python
# In the request handler (per-request, per-thread)
encoding = negotiate_encoding(accept_encoding_header)
compressor = create_compressor(encoding, config)  # fresh instance
compressed = compressor.compress(body)
compressed += compressor.flush()
```

**Why it works.** With no sharing, there is no contention. Each thread owns
its compressor for the lifetime of one request. When the request completes,
the compressor is garbage collected. No reset logic, no cleanup bugs, no
use-after-return errors.

**Anti-pattern.** Object pooling with locks. Under free-threading, a pool of
reusable compressors protected by a lock creates contention at both checkout
and checkin. The lock cost often exceeds the allocation cost, especially for
lightweight objects.

**When pooling is still justified.** Pool only when construction is genuinely
expensive (database connections, TLS handshakes) and the object is long-lived.
For anything that lives for a single request, fresh allocation wins.

**Generalization.** JSON encoders, template renderers, serialization buffers,
validation contexts -- create fresh, use once, discard. Modern allocators
make this cheap. Free-threading makes it necessary.

---

### 9. Monotonic ID Generation

**The pattern.** Use a lock-protected counter for globally unique IDs. Keep
the critical section minimal.

```python
_id_counter = 0
_id_lock = threading.Lock()

def next_connection_id() -> int:
    """Globally unique, monotonically increasing connection ID."""
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return _id_counter
```

**Why it works.** The lock protects a single integer increment -- the critical
section is nanoseconds. This is one of the few places where a lock is the
right tool, because the shared state (the counter) genuinely must be mutated
by multiple threads and must never produce duplicates.

**In Pounce.** Every accepted connection gets a unique `connection_id` from
this generator. The ID appears in lifecycle events, access logs, and error
traces, enabling correlation across threads.

**Generalization.** Request IDs, trace IDs, sequence numbers for ordered
delivery, epoch counters for optimistic concurrency -- any global counter
that must be unique across threads follows this pattern.

---

### 10. Adaptive Runtime Detection

**The pattern.** Check the GIL state once at startup. Branch your concurrency
strategy based on the result. Never check at runtime per-request.

```python
def is_gil_enabled() -> bool:
    return getattr(sys, "_is_gil_enabled", lambda: True)()

def detect_worker_mode() -> WorkerMode:
    return WorkerMode.PROCESS if is_gil_enabled() else WorkerMode.THREAD
```

**Why it works.** A single boolean check at startup selects the entire
concurrency strategy. The same codebase, the same tests, and the same CI
pipeline work on both GIL and free-threaded builds. No `#ifdef`, no
separate branches, no conditional imports.

**In Pounce.** The supervisor calls `detect_worker_mode()` once. On 3.14t
(nogil), it spawns worker threads that share the interpreter. On GIL builds,
it spawns worker processes via `multiprocessing`. The worker implementation
is identical in both cases -- only the spawning differs.

**Key design rule.** Use feature detection, not version checking:

```python
# Correct: detect capability
if not is_gil_enabled():
    spawn_threads()

# Wrong: check version (breaks on 3.14 non-t builds)
if sys.version_info >= (3, 14):
    spawn_threads()
```

**Generalization.** This pattern applies to any capability that varies across
Python builds: `asyncio` backends, memory allocators, JIT availability. Detect
the capability, branch once, and run a uniform code path thereafter.

---

## Summary

| # | Pattern | Thread-Safety Guarantee | Lock Required |
|---|---------|------------------------|---------------|
| 1 | Frozen Configuration | Immutable after construction -- no write races possible | None |
| 2 | Immutable Events | Frozen value objects -- safe to share across any number of readers | None (at event level) |
| 3 | Sans-I/O Protocols | Thread-local instances -- no sharing by construction | None |
| 4 | Queue-Based Handoff | `queue.Queue` internal synchronization -- ownership transfer | Built into Queue |
| 5 | Accept Distributor | Single writer (accept thread) + Queue -- no thundering herd | Built into Queue |
| 6 | C Extension Audit | Process-level -- one bad import disables free-threading globally | N/A (prevention) |
| 7 | Functional State Machine | Immutable state + pure reducer -- no mutation possible | None |
| 8 | Per-Request Instances | Thread-local by lifetime -- never shared | None |
| 9 | Monotonic ID Generation | Minimal critical section -- lock held for one integer increment | `threading.Lock` |
| 10 | Adaptive Detection | Decided once at startup -- no per-request branching | None |

**The overarching principle:** free-threading rewards architectures that minimize
shared mutable state. Eight of the ten patterns above require zero locks. The
remaining two (ID generation and queue handoff) confine locking to the smallest
possible scope. This is not a coincidence -- it is the design target.

If you find yourself reaching for `threading.Lock` on a hot path, step back and
ask: can this state be frozen, this object be per-request, or this communication
be a queue? In free-threaded Python, the fastest lock is the one you eliminated.
