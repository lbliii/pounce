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

# Bengal static-site workload
python benchmarks/run_benchmark.py --workload bengal --workers 1 --duration 10

# Chirp/LB Sonic-shaped forum workload
python benchmarks/run_benchmark.py --workload chirp --workers 4 --duration 30

# Repeat each workload for artifact variance
python benchmarks/run_benchmark.py --workload chirp --repeat 5 --artifact-output artifacts/chirp.json

# Compare against uvicorn
python benchmarks/run_benchmark.py --compare --workers 4

# Save structured runner output as JSON
python benchmarks/run_benchmark.py --workload all --output results.json

# Save artifact-schema-compatible metadata for PR/release evidence
python benchmarks/run_benchmark.py --workload chirp --artifact-output artifacts/chirp.json
```

### Workloads

| Workload | App | Description |
|----------|-----|-------------|
| `hello` | `benchmarks.apps.hello:app` | Minimal hello-world (measures server overhead) |
| `json` | `benchmarks.apps.json_app:app` | JSON response (pre-serialized) |
| `echo` | `benchmarks.apps.echo:app` | POST body echo (1KB payload) |
| `bengal` | `benchmarks.apps.bengal_static:app` | Bengal-shaped generated static site |
| `chirp` | `benchmarks.apps.chirp_forum:app` | Chirp/LB Sonic-shaped multi-tenant forum thread |

### Runner Options

| Flag | Default | Description |
|------|---------|-------------|
| `--workload` | `hello` | Workload name or `all` |
| `--workers` | `1` | Pounce worker count |
| `--duration` | `10` | Test duration in seconds |
| `--threads` | `4` | Load generator thread count |
| `--connections` | `100` | Concurrent connections |
| `--repeat` | `1` | Repeat each workload and label each sample in the output |
| `--compare` | off | Also benchmark uvicorn |
| `--output` | none | Save structured runner output to JSON. This is not a benchmark artifact unless it contains the metadata required by `artifact-schema.json`. |
| `--artifact-output` | none | Save artifact-schema-compatible metadata for PR/release evidence. |

## Benchmark Artifacts

Public numeric performance claims need a reproducible artifact or an explicit
snapshot caveat. Store benchmark artifacts as JSON that follows
`benchmarks/artifact-schema.json`.

The `run_benchmark.py --output` file is structured runner output. Treat it as a
raw input for analysis. Use `--artifact-output` when a PR or release needs
metadata shaped for `benchmarks/artifact-schema.json`.

Required metadata:

- command and server command
- git SHA
- workload
- Python version and GIL mode
- OS and hardware
- worker mode and worker count
- duration, connections, and load-generator threads
- load tool and version
- comparison target and version, when comparing
- sample count and grouped variance by server, workload, and worker count
- raw output path
- summary table

If a doc or release note uses a number without an artifact, phrase it as a local
snapshot, tuning example, or historical note. Do not promote it to a product
claim.

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
