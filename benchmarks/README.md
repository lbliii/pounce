# Pounce Benchmarks

Throughput, memory, and streaming stress tests for pounce.

## Automated Benchmarks (pytest)

Run all benchmarks:

```bash
uv run pytest benchmarks/ -m benchmark -v -s
```

### What the automated suite tests

| Test | File | What it validates |
|------|------|-------------------|
| Single-worker throughput | `test_throughput.py` | Baseline req/s with one worker |
| Multi-worker throughput | `test_throughput.py` | Multiple workers handle concurrent load |
| Thread worker memory | `test_memory.py` | RSS with 4 thread workers (shared interpreter) |
| Process worker memory | `test_memory.py` | RSS with 4 process workers (forked) |
| Thread vs process comparison | `test_memory.py` | Thread mode doesn't blow up RSS |
| SSE stress test | `test_sse_stress.py` | 100 concurrent streams held 10s, no memory leak |
| Chirp compatibility | `test_chirp_compat.py` | Chirp App serves via pounce Worker (skips if chirp not installed) |

### Latest Results (Python 3.14.2t, macOS, Apple Silicon)

```
Throughput:
  [single-worker] ~6-7k req/s, p50=6.6ms, p99=13.7ms
  [multi-worker]  ~7k req/s (2 workers, shared socket)

Memory (4 workers):
  [thread workers]  delta ~3MB (shared interpreter)
  [process workers] delta ~0MB (measured from parent; child RSS not reflected in ru_maxrss)

SSE Stress:
  [100 connections] held 10s, ~20k total events, RSS growth < 3MB
```

Note: These are test-environment numbers with modest load (500 requests, 50
concurrency). True throughput scaling at production loads will be validated
with wrk/hey in Phase 4.

## Manual Benchmarks (wrk / hey)

For production-grade throughput numbers, use an external benchmarking tool.

### Prerequisites

```bash
# wrk (recommended)
brew install wrk

# or hey
go install github.com/rakyll/hey@latest
```

### Quick Start

```bash
# Start pounce with the benchmark app
pounce benchmarks.hello_app:app --workers 0 --no-access-log

# In another terminal, run the benchmark
wrk -t4 -c100 -d10s http://127.0.0.1:8000/
```

### Comparison Benchmarks

#### Single worker

```bash
# Pounce (1 worker)
pounce benchmarks.hello_app:app --workers 1 --no-access-log

# Uvicorn (1 worker)
uvicorn benchmarks.hello_app:app --host 127.0.0.1 --port 8001 --no-access-log
```

#### Multi-worker

```bash
# Pounce (4 workers, threads on nogil)
pounce benchmarks.hello_app:app --workers 4 --no-access-log

# Uvicorn (4 workers, processes)
uvicorn benchmarks.hello_app:app --host 127.0.0.1 --port 8001 --workers 4 --no-access-log
```

### SSE Streaming

```bash
# Start the SSE app
pounce benchmarks.sse_app:app --workers 4 --no-access-log

# Hold open N concurrent SSE connections (use curl or a custom client)
for i in $(seq 1 100); do
  curl -N http://127.0.0.1:8000/events &
done
```

### Performance Targets (Phase 4)

| Metric | Target |
|--------|--------|
| Single-worker req/s | > 15,000 |
| 4-worker req/s (nogil) | > 50,000 |
| p99 latency at 10k req/s | < 5ms |
| Memory (1 worker) | < 20MB RSS |
| Memory (4 workers, threads) | < 30MB RSS |
