# Pounce Benchmarks

Throughput and latency benchmarks for pounce.

## Prerequisites

Install a benchmarking tool:

```bash
# wrk (recommended)
brew install wrk

# or hey
go install github.com/rakyll/hey@latest
```

## Quick Start

```bash
# Start pounce with the benchmark app
pounce benchmarks.hello_app:app --workers 0 --no-access-log

# In another terminal, run the benchmark
wrk -t4 -c100 -d10s http://127.0.0.1:8000/
```

## Comparison Benchmarks

### Single worker

```bash
# Pounce (1 worker)
pounce benchmarks.hello_app:app --workers 1 --no-access-log

# Uvicorn (1 worker)
uvicorn benchmarks.hello_app:app --host 127.0.0.1 --port 8001 --no-access-log
```

### Multi-worker

```bash
# Pounce (4 workers, threads on nogil)
pounce benchmarks.hello_app:app --workers 4 --no-access-log

# Uvicorn (4 workers, processes)
uvicorn benchmarks.hello_app:app --host 127.0.0.1 --port 8001 --workers 4 --no-access-log
```

## What to Measure

| Metric | Target (Phase 2) |
|--------|-------------------|
| Single-worker req/s | > 15,000 |
| 4-worker req/s (nogil) | > 50,000 |
| p99 latency at 10k req/s | < 5ms |
| Memory (1 worker) | < 20MB RSS |
| Memory (4 workers, threads) | < 30MB RSS |

## SSE Streaming Stress Test

For sustained streaming connections:

```bash
# Start the SSE app (TODO: implement sse_app.py)
pounce benchmarks.sse_app:app --workers 4 --no-access-log

# Hold open N concurrent SSE connections
# (requires a streaming benchmark tool)
```
