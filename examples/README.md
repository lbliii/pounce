# Pounce Examples

Standalone ASGI applications that showcase pounce features.  Each file is
self-contained — no extra dependencies beyond pounce itself (except
`chirp_app.py` which needs chirp).

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

## Running as Smoke Tests

These examples also serve as integration smoke tests:

```bash
uv run pytest tests/integration/test_examples.py -v
```

## Benchmarking

The `benchmarks/` directory imports apps from `examples/` as benchmark
targets.  See [benchmarks/README.md](../benchmarks/README.md) for
instructions on running throughput and memory benchmarks.
