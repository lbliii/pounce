# Pounce Examples

Standalone ASGI applications that showcase pounce features.  Each file is
self-contained — no extra dependencies beyond pounce itself (except
`chirp_app.py` which needs chirp and `websocket_chat.py` which needs
wsproto via `pounce[ws]`).

## Quick Start

```bash
# Run any example
pounce examples.<name>:app

# For example
pounce examples.hello:app
```

Then visit <http://127.0.0.1:8000/>.

## Examples

| Example | What it shows | Run command |
|---------|---------------|-------------|
| `hello.py` | Minimal ASGI app — the "start here" example | `pounce examples.hello:app` |
| `lifespan.py` | Startup/shutdown hooks, thread-safe shared state | `pounce examples.lifespan:app` |
| `streaming_sse.py` | Server-Sent Events with named events and JSON payloads | `pounce examples.streaming_sse:app` |
| `compression_demo.py` | Automatic zstd/gzip content-encoding negotiation | `pounce examples.compression_demo:app` |
| `cpu_parallel.py` | CPU-bound work — the free-threading showcase | `pounce examples.cpu_parallel:app --workers 4` |
| `websocket_chat.py` | Multi-client chat room — shared state under free-threading | `pounce examples.websocket_chat:app --workers 4` |
| `file_upload.py` | File upload with chunked body reading and backpressure | `pounce examples.file_upload:app --server-timing` |
| `mini_router.py` | Middleware and routing on raw ASGI | `pounce examples.mini_router:app` |
| `chirp_app.py` | Chirp framework integration | `pounce examples.chirp_app:app` |

## Free-Threading Demo

The `cpu_parallel.py` example is designed to show pounce's free-threading
advantage.  Run it with different worker counts and compare throughput:

```bash
# Single worker (baseline)
pounce examples.cpu_parallel:app --workers 1 --no-access-log

# Multi-worker (threads on 3.14t, processes on GIL builds)
pounce examples.cpu_parallel:app --workers 4 --no-access-log
```

Then benchmark:

```bash
wrk -t4 -c100 -d10s http://127.0.0.1:8000/
```

On Python 3.14t you should see near-linear scaling because threads share the
interpreter and run without the GIL.

## Verifying Free-Threading

Check that you are running a free-threaded build:

```bash
python -c "import sys; print('GIL enabled:', sys._is_gil_enabled())"
# Should print: GIL enabled: False
```

## Compression Demo

Test different encodings against `compression_demo.py`:

```bash
# zstd (preferred — Python 3.14 stdlib)
curl -s -H "Accept-Encoding: zstd" -D - http://127.0.0.1:8000/ | head -20

# gzip (universal fallback)
curl -s -H "Accept-Encoding: gzip" -D - http://127.0.0.1:8000/ | head -20

# no compression
curl -s -H "Accept-Encoding: identity" http://127.0.0.1:8000/
```

Look for the `Content-Encoding` response header to see which encoding was
selected.

## WebSocket Chat Room

The `websocket_chat.py` example is the most compelling free-threading demo.
Multiple browser tabs connect via WebSocket and chat in real time.  The
shared room state is protected by `threading.Lock` — on 3.14t with
`--workers 4`, clients on different worker threads can still talk to each
other through shared memory.

```bash
pounce examples.websocket_chat:app --workers 4
```

Then open multiple tabs at <http://127.0.0.1:8000/> and start chatting.

## File Upload

The `file_upload.py` example demonstrates chunked request body reading and
pounce's backpressure mechanism.  Upload a file via the browser form or curl:

```bash
# Upload a 10 MB random file
dd if=/dev/urandom bs=1M count=10 2>/dev/null | \
    curl -X POST -H "Content-Type: application/octet-stream" \
         --data-binary @- http://127.0.0.1:8000/upload
```

The response reports bytes received, chunk count, and throughput.  Use
`--server-timing` to see parse/processing timing in browser DevTools.

## Middleware and Routing

The `mini_router.py` example builds a ~50-line request router on raw ASGI
to show that routing and middleware are just function composition:

```bash
curl http://127.0.0.1:8000/              # welcome JSON
curl http://127.0.0.1:8000/users/42      # path parameter extraction
curl -X POST -d "hi" http://127.0.0.1:8000/echo  # body echo
curl http://127.0.0.1:8000/nonexistent   # 404
```

For real applications, use chirp — it provides this and much more with
proper type safety and composable middleware.

## Running as Smoke Tests

These examples also serve as integration smoke tests:

```bash
uv run pytest tests/integration/test_examples.py -v
```

## Benchmarking

The `benchmarks/` directory imports apps from `examples/` as benchmark
targets.  See [benchmarks/README.md](../benchmarks/README.md) for
instructions on running throughput and memory benchmarks.
