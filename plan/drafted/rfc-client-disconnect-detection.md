# RFC: HTTP/1.1 Client Disconnect Detection for Streaming Responses

**Status**: Draft
**Date**: 2026-02-08
**Scope**: `pounce/asgi/bridge.py`, `pounce/worker.py`

---

## Problem

When a client disconnects mid-stream on an HTTP/1.1 connection (e.g. browser refresh during SSE, tab close during chunked download), Pounce does not detect the disconnect or signal it to the ASGI application. This causes two cascading failures:

1. **The ASGI app never receives `http.disconnect`.** The `receive()` callable created by `create_empty_receive()` blocks forever after the first call. It never returns `{"type": "http.disconnect"}`, which is required by the ASGI spec (§2.1.3).

2. **Writes to a dead socket log noisy warnings.** The app's generator keeps producing events and calling `send()`, which calls `writer.write()` on a closed transport. After 5 failed writes, Python's `asyncio` internals emit `socket.send() raised exception.` — one warning per write, indefinitely.

### Evidence

**Pounce — `create_empty_receive()`** blocks forever on the second call:

```python
# src/pounce/asgi/bridge.py:122-134
def create_empty_receive() -> Receive:
    called = False
    async def receive() -> dict[str, Any]:
        nonlocal called
        if not called:
            called = True
            return _EMPTY_BODY_MESSAGE
        # Block forever — the app shouldn't call receive() again
        await asyncio.Event().wait()
        return _EMPTY_BODY_MESSAGE  # unreachable
    return receive
```

**Chirp — `handle_sse()`** already monitors for disconnect correctly, but never receives the signal:

```python
# chirp/src/chirp/realtime/sse.py:51-56
async def monitor_disconnect() -> None:
    while not disconnected.is_set():
        message = await receive()
        if message.get("type") == "http.disconnect":
            disconnected.set()
            return
```

**Worker — `_handle_request()`** runs the ASGI app as a single await with no concurrent disconnect monitoring:

```python
# src/pounce/worker.py:417-421
if body_complete:
    try:
        await self._app(scope, receive, send)
```

**asyncio internals** — the warning source (`selector_events.py:1055-1079`):

```python
if self._conn_lost:
    if self._conn_lost >= constants.LOG_THRESHOLD_FOR_CONNLOST_WRITES:  # = 5
        logger.warning('socket.send() raised exception.')
    self._conn_lost += 1
    return
```

### Impact

- **All HTTP/1.1 streaming responses** (SSE, chunked downloads, long-poll) are affected.
- HTTP/2 is **not affected** — `H2StreamReset` events already cancel per-stream tasks.
- WebSocket is **not affected** — the frame reader task detects disconnect and pushes `websocket.disconnect`.
- Every client disconnect produces an unbounded stream of `socket.send() raised exception.` warnings in the log until the generator naturally stops or the server shuts down.

---

## Goals

1. Deliver `{"type": "http.disconnect"}` to the ASGI app when the client drops.
2. Stop the ASGI app task promptly after disconnect (cancel if it doesn't exit).
3. Eliminate the `socket.send() raised exception.` log noise.
4. Zero overhead on short-lived request/response cycles (the common case).

### Non-Goals

- Changing the HTTP/2 or WebSocket disconnect paths (they already work).
- Adding a public "connection health" API beyond the ASGI spec.
- Modifying Chirp — it already handles `http.disconnect` correctly.

---

## Design Options

### Option A: Concurrent Reader Task (Recommended)

Mirror the WebSocket handler pattern. Spawn a reader task alongside the app task that monitors the socket for client disconnect.

**Architecture:**

```
┌─────────────┐       ┌──────────────────┐
│ Reader Task │       │    App Task      │
│             │       │                  │
│ reader.read │       │ scope, receive,  │
│   (socket)  │──EOF──│    send          │
│             │  ┌──► │                  │
│  detect     │  │    │ receive() called │
│  disconnect │  │    │  → returns       │
│             │  │    │  http.disconnect  │
│  set event ─┼──┘    │                  │
│             │       │ (or cancelled    │
│             │       │  after grace     │
│             │       │  period)         │
└─────────────┘       └──────────────────┘
        │                      │
        └──────┬───────────────┘
               ▼
         asyncio.wait
         FIRST_COMPLETED
```

**Changes:**

1. **New `create_disconnect_receive()`** in `bridge.py` — replaces `create_empty_receive()` for bodyless requests. Returns `_EMPTY_BODY_MESSAGE` on first call, then waits on a disconnect `asyncio.Event`. When the event fires, returns `{"type": "http.disconnect"}`.

2. **Reader task in `_handle_request()`** — for bodyless requests, spawn a lightweight task that calls `reader.read()` in a loop. If it gets empty bytes (client disconnected), set the disconnect event.

3. **Cancellation with grace period** — after the reader detects disconnect, wait briefly (e.g. 1s) for the app to exit cleanly (via `http.disconnect`). If it doesn't, cancel the app task.

4. **Guard `send()` against closed connections** — before calling `writer.write()`, check `writer.is_closing()`. If closing, silently return (or raise a suppressed error). This prevents the asyncio warning immediately.

**Pros:**
- Consistent with the existing WebSocket handler pattern.
- ASGI-spec compliant — apps get proper `http.disconnect`.
- Grace period allows cooperative shutdown before forced cancellation.
- The reader task adds negligible overhead — it's one blocked `read()` per connection.

**Cons:**
- Adds task management complexity to `_handle_request()`.
- The reader task holds a reference to the StreamReader during the app's lifetime.

### Option B: Transport `connection_lost` Callback

Hook into `asyncio.Protocol.connection_lost()` on the transport to detect disconnect at the transport layer, then propagate to the receive callable.

**Changes:**

1. Register a callback on the transport via `writer.transport.set_protocol()` or a wrapper.
2. When `connection_lost()` fires, set a disconnect event.
3. The receive callable returns `http.disconnect` when the event is set.

**Pros:**
- Truly event-driven — no polling or extra reads.
- Zero extra tasks per connection.

**Cons:**
- Requires reaching into asyncio transport internals.
- `connection_lost()` is called on the transport's protocol, which is the `asyncio.StreamReaderProtocol` — replacing or wrapping it risks breaking reader/writer behavior.
- Less portable across asyncio implementations (e.g., uvloop).
- Not consistent with pounce's existing pattern of using StreamReader/StreamWriter.

### Option C: Send-Side Guard Only

Don't deliver `http.disconnect` at all. Just guard `writer.write()` against closed transports.

**Changes:**

1. In `create_send()`, check `writer.is_closing()` before each `writer.write()`. If closing, skip the write and set a flag.
2. The ASGI app eventually notices send() doing nothing and stops (or doesn't — it's up to the app).

**Pros:**
- Minimal code change.
- Eliminates the noisy warnings immediately.

**Cons:**
- **Not ASGI-compliant** — apps that rely on `http.disconnect` (like Chirp) will never see it.
- The ASGI app keeps running indefinitely, wasting CPU on generating events nobody will see.
- Only hides the symptom, doesn't fix the root cause.
- Chirp's SSE `monitor_disconnect()` stays blocked forever — the producer task runs until server shutdown.

---

## Recommendation: Option A (Concurrent Reader Task)

Option A is the clear winner:

| Criterion           | A: Reader Task | B: Transport Hook | C: Send Guard |
|---------------------|:--------------:|:-----------------:|:-------------:|
| ASGI-compliant      | Yes            | Yes               | No            |
| App gets signal     | Yes            | Yes               | No            |
| Stops wasted CPU    | Yes            | Yes               | No            |
| Eliminates warnings | Yes            | Yes               | Yes           |
| Consistent pattern  | Yes (WS)       | No                | n/a           |
| Implementation risk | Low            | Medium            | Low           |
| Zero overhead (fast path) | Yes      | Yes               | Yes           |

Additionally, Option C's send-side guard should be adopted as a **defense-in-depth measure** alongside Option A. Even with proper disconnect detection, race conditions can cause a few writes to a closing transport. Guarding `writer.write()` prevents those from producing warnings.

---

## Implementation Plan

### Phase 1: Disconnect-Aware Receive (bridge.py)

Create `create_disconnect_receive()` that replaces `create_empty_receive()` for the bodyless request path.

```python
def create_disconnect_receive(
    disconnect: asyncio.Event,
) -> Receive:
    """Create a receive callable that delivers http.disconnect.

    For bodyless requests (GET, HEAD, etc.): returns the empty body message
    on first call, then waits for the disconnect event before returning
    http.disconnect.
    """
    called = False

    async def receive() -> dict[str, Any]:
        nonlocal called
        if not called:
            called = True
            return _EMPTY_BODY_MESSAGE
        # Wait until client disconnects
        await disconnect.wait()
        return {"type": "http.disconnect"}

    return receive
```

Also create a disconnect-aware variant for body requests that returns `http.disconnect` after the body is fully received:

```python
def create_receive_with_disconnect(
    body_events: asyncio.Queue[BodyReceived],
    disconnect: asyncio.Event,
) -> Receive:
    """Create a receive callable that delivers body events then http.disconnect."""
    body_complete = False

    async def receive() -> dict[str, Any]:
        nonlocal body_complete
        if not body_complete:
            event = await body_events.get()
            if not event.more:
                body_complete = True
            return {
                "type": "http.request",
                "body": event.data,
                "more_body": event.more,
            }
        # Body done — wait for disconnect
        await disconnect.wait()
        return {"type": "http.disconnect"}

    return receive
```

### Phase 2: Connection Monitor Task (worker.py)

Add a `_monitor_disconnect()` coroutine and integrate it into `_handle_request()`:

```python
async def _monitor_disconnect(
    self,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    disconnect: asyncio.Event,
) -> None:
    """Monitor the TCP connection for client disconnect.

    Reads from the socket to detect when the client closes the connection.
    Sets the disconnect event to signal the receive callable.
    """
    try:
        while True:
            data = await reader.read(1)
            if not data:
                # Client disconnected — EOF
                break
    except (ConnectionError, OSError):
        pass
    finally:
        disconnect.set()
```

Modify `_handle_request()` to use concurrent tasks:

```python
# In _handle_request, replace the simple await with:
disconnect = asyncio.Event()
receive = create_disconnect_receive(disconnect)

app_task = asyncio.create_task(self._app(scope, receive, send))
monitor_task = asyncio.create_task(
    self._monitor_disconnect(reader, writer, disconnect)
)

done, pending = await asyncio.wait(
    {app_task, monitor_task},
    return_when=asyncio.FIRST_COMPLETED,
)

for task in pending:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
```

### Phase 3: Send-Side Guard (bridge.py)

Add a `writer.is_closing()` check to the send callable as defense-in-depth:

```python
# In create_send(), inside the http.response.body handler:
if writer.is_closing():
    return  # Connection lost — silently discard
```

### Phase 4: Tests

1. **`test_disconnect_receive`** — verify `create_disconnect_receive()` returns empty body, then `http.disconnect` after event is set.
2. **`test_disconnect_receive_with_body`** — verify body events flow through, then `http.disconnect`.
3. **`test_streaming_disconnect`** — integration test: start an SSE-like app, close the client connection, verify the app task is cancelled.
4. **`test_send_guard_closed_writer`** — verify send() silently returns when writer is closing.
5. **`test_short_response_no_overhead`** — verify simple GET responses don't create unnecessary tasks (fast path preserved).

### Phase 5: Fast-Path Optimization

For non-streaming responses (the common case), the concurrent reader task is unnecessary overhead. Add a fast-path: only spawn the monitor task when the response uses `more_body=True` (streaming).

This can be done by deferring monitor creation: start the monitor lazily when the first streaming body chunk is sent, not at request start. This keeps the zero-overhead guarantee for standard request/response cycles.

---

## Migration & Compatibility

- **No breaking changes.** The ASGI interface is unchanged — this adds behavior that was always specified but never delivered.
- **Frameworks benefit automatically.** Chirp's SSE handler will start receiving `http.disconnect` without any changes.
- **Apps that ignore `receive()` after body** (most simple apps) are unaffected — the monitor task is cancelled when the app completes normally.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Reader task reads data meant for next request (keep-alive) | Low | Only active during app execution; worker already manages the keep-alive loop separately. Reader task is cancelled when app completes. |
| Monitor task leaks on abnormal app exit | Low | `asyncio.wait` + cancel pattern (same as WebSocket handler) ensures cleanup. |
| Extra task per connection hurts throughput | Low | The task does one blocking `read()` — negligible CPU. Benchmarks should confirm. |
| Race between disconnect detection and final write | Low | Send-side guard (Phase 3) handles the race window. |

---

## Open Questions

1. **Grace period before cancellation?** Should we wait N seconds after delivering `http.disconnect` before cancelling the app task? Chirp's SSE handler would exit quickly, but other apps might need time for cleanup. Proposed: 5s grace period, configurable via `ServerConfig`.

2. **Should the monitor task read with a timeout?** A keep-alive timeout already exists at the connection level. The monitor task could respect `keep_alive_timeout` or use a separate streaming timeout. Proposed: no timeout on the monitor read — it blocks until disconnect or app completion.

3. **Lazy vs eager monitor creation?** Phase 5 proposes lazy creation (only when streaming detected). Alternative: always create the monitor, accept the minimal overhead. Proposed: start with eager creation for simplicity, optimize later if benchmarks warrant it.
