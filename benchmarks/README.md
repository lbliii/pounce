# Pounce Benchmarks

Throughput, memory, and streaming stress tests for pounce.

## Quick Start

### Automated Benchmark Suite (Phase 4)

The benchmark runner starts pounce, drives load with wrk or hey, and captures
structured results.

```bash
# Prerequisites
brew install wrk    # or: go install github.com/rakyll/hey@latest

# Quick benchmark (hello-world, 10s, 1 worker)
python benchmarks/run_benchmark.py

# Full suite, all workloads, 4 workers
python benchmarks/run_benchmark.py --workload all --workers 4 --duration 30

# Compare against uvicorn
python benchmarks/run_benchmark.py --compare --workers 4

# Save results as JSON
python benchmarks/run_benchmark.py --workload all --output results.json
```

### Workloads

| Workload | App | Description |
|----------|-----|-------------|
| `hello` | `benchmarks.apps.hello:app` | Minimal hello-world (measures server overhead) |
| `json` | `benchmarks.apps.json_app:app` | JSON response (pre-serialized) |
| `echo` | `benchmarks.apps.echo:app` | POST body echo (1KB payload) |

### Runner Options

| Flag | Default | Description |
|------|---------|-------------|
| `--workload` | `hello` | Workload name or `all` |
| `--workers` | `1` | Pounce worker count |
| `--duration` | `10` | Test duration in seconds |
| `--threads` | `4` | Load generator thread count |
| `--connections` | `100` | Concurrent connections |
| `--compare` | off | Also benchmark uvicorn |
| `--output` | none | Save results to JSON file |

## Pytest Benchmarks

Run in-process benchmarks via pytest:

```bash
uv run pytest benchmarks/ -m benchmark -v -s
```

### What the pytest suite tests

| Test | File | What it validates |
|------|------|-------------------|
| Single-worker throughput | `test_throughput.py` | Baseline req/s with one worker |
| Multi-worker throughput | `test_throughput.py` | Multiple workers handle concurrent load |
| Thread worker memory | `test_memory.py` | RSS with 4 thread workers (shared interpreter) |
| Process worker memory | `test_memory.py` | RSS with 4 process workers (forked) |
| Thread vs process comparison | `test_memory.py` | Thread mode doesn't blow up RSS |
| SSE stress test | `test_sse_stress.py` | 100 concurrent streams held 10s, no memory leak |
| Chirp compatibility | `test_chirp_compat.py` | Chirp App serves via pounce Worker (skips if chirp not installed) |

## Manual Benchmarks (wrk / hey)

For ad-hoc testing without the runner:

```bash
# Start pounce
pounce benchmarks.apps.hello:app --workers 0 --no-access-log --no-compression

# In another terminal
wrk -t4 -c100 -d10s http://127.0.0.1:8000/
```

### Comparison Benchmarks

#### Single worker

```bash
# Pounce (1 worker)
pounce benchmarks.apps.hello:app --workers 1 --no-access-log --no-compression

# Uvicorn (1 worker)
uvicorn benchmarks.apps.hello:app --host 127.0.0.1 --port 8001 --no-access-log
```

#### Multi-worker

```bash
# Pounce (4 workers, threads on nogil)
pounce benchmarks.apps.hello:app --workers 4 --no-access-log --no-compression

# Uvicorn (4 workers, processes)
uvicorn benchmarks.apps.hello:app --host 127.0.0.1 --port 8001 --workers 4 --no-access-log
```

## Performance Targets (Phase 4)

| Metric | Target |
|--------|--------|
| Single-worker req/s | > 15,000 |
| 4-worker req/s (nogil) | > 50,000 |
| p99 latency at 10k req/s | < 5ms |
| Memory (1 worker) | < 20MB RSS |
| Memory (4 workers, threads) | < 30MB RSS |

## Profiling

### Hot-path flame graph

```bash
# Start pounce under load, then attach py-spy
pounce benchmarks.apps.hello:app --workers 1 --no-access-log --no-compression &
PID=$!
wrk -t4 -c100 -d30s http://127.0.0.1:8000/ &
py-spy record -o flame.svg --pid $PID --duration 20
kill $PID
```

### Memory profiling

```bash
python benchmarks/profile_memory.py --workers 4 --duration 30
```
