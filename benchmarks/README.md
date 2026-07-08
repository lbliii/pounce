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

# Compare against uvicorn, Hypercorn, and Granian
python benchmarks/run_benchmark.py --servers pounce,uvicorn,hypercorn,granian --workers 4

# Sustained fixed-rate evidence with p50/p99/p999 and RSS/CPU over time
python benchmarks/run_benchmark.py --workload chirp --workers 4 --duration 120 \
    --repeat 3 --rate 1000 --servers pounce,uvicorn,hypercorn,granian \
    --artifact-output artifacts/chirp-sustained.json

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
| `bengal` | `benchmarks.apps.bengal_static:app` | Bengal-shaped generated static site home page |
| `bengal_asset` | `benchmarks.apps.bengal_static:app` | Bengal-shaped generated static site CSS asset |
| `bengal_feed` | `benchmarks.apps.bengal_static:app` | Bengal-shaped generated static site XML feed |
| `bengal_post` | `benchmarks.apps.bengal_static:app` | Bengal-shaped generated static site post page |
| `chirp` | `benchmarks.apps.chirp_forum:app` | Chirp/LB Sonic-shaped multi-tenant forum thread |
| `chirp_asset` | `benchmarks.apps.chirp_forum:app` | Chirp/LB Sonic-shaped forum CSS asset |
| `chirp_events` | `benchmarks.apps.chirp_forum:app` | Chirp/LB Sonic-shaped forum SSE first event |
| `chirp_home` | `benchmarks.apps.chirp_forum:app` | Chirp/LB Sonic-shaped multi-tenant forum home |

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
| `--servers` | `pounce` | Comma-separated server set. Supports `pounce`, `uvicorn`, `hypercorn`, and `granian`; overrides `--compare`. |
| `--rate` | none | Use the built-in fixed-rate driver at this scheduled RPS. Includes scheduler delay to avoid coordinated omission and reports p999. |
| `--output` | none | Save structured runner output to JSON. This is not a benchmark artifact unless it contains the metadata required by `artifact-schema.json`. |
| `--artifact-output` | none | Save artifact-schema-compatible metadata for PR/release evidence. |
| `--compare-baseline` | none | Regression gate: diff this run against a committed baseline artifact and exit non-zero on regression (see below). |
| `--rps-tolerance` | `0.10` | Allowed fractional median req/s drop before the gate fails. |
| `--p99-tolerance` | `0.20` | Allowed fractional median p99 latency rise before the gate fails. |

## Benchmark Artifacts

Public numeric performance claims need a reproducible artifact or an explicit
snapshot caveat. Store benchmark artifacts as JSON that follows
`benchmarks/artifact-schema.json`.

The `run_benchmark.py --output` file is structured runner output. Treat it as a
raw input for analysis. Use `--artifact-output` when a PR or release needs
metadata shaped for `benchmarks/artifact-schema.json`.

### Authoritative pipeline vs the `pounce bench` snapshot

`benchmarks/run_benchmark.py` is the authoritative, governed pipeline: it is the
only driver that emits schema-compatible artifacts (`--artifact-output`) with a
git SHA, repeated-sample variance, raw load-tool output, and under-load process
telemetry. Cite its `--artifact-output` JSON for any public numeric claim.

The `pounce bench` CLI command is a convenience driver only. It uses an
`http.client` thread driver, prints a plain-text table explicitly labelled a
local snapshot, and does **not** emit an artifact. Use it for quick local
sanity checks, never as benchmark evidence.

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
- per-sample server RSS when the platform exposes it
- under-load process telemetry (`telemetry`): peak RSS summed across the
  root server and any forked worker processes, mean/peak aggregate CPU%, the
  observed worker pids, and a per-process CPU/RSS time series for each repeated
  benchmark sample; best-effort with null summaries or empty point lists when
  the platform does not expose it
- raw load-tool stdout/stderr entries per sample
- summary table

If a doc or release note uses a number without an artifact, phrase it as a local
snapshot, tuning example, or historical note. Do not promote it to a product
claim.

Current local snapshot artifacts:

| Artifact | Scope | Caveat |
|----------|-------|--------|
| `benchmarks/artifacts/2026-05-22/bengal-pounce-local.json` | Bengal home page, pounce-only, 5 samples, 5s each | Local macOS/free-threaded run; use as investigation input, not a release claim. |
| `benchmarks/artifacts/2026-05-22/chirp-pounce-local.json` | Chirp thread page, pounce-only, 5 samples, 5s each | Local macOS/free-threaded run; no uvicorn comparison. |
| `benchmarks/artifacts/2026-07-08/process-cpu-local.json` | Hello workload, pounce-only, 2 process-worker samples with per-process CPU/RSS series | Local macOS/GIL-enabled proof run; validates telemetry capture, not a release performance claim. |
| `benchmarks/artifacts/2026-07-08/http3-pounce-local.json` | HTTP/3 hello response over 4 persistent QUIC connections, 5 samples, 5s each | Local macOS/Python 3.14t run; protocol snapshot only, with no HTTP/2 comparison or product-level performance claim. |

## Scheduled and Release Evidence

`.github/workflows/benchmarks.yml` runs the fixed-rate driver weekly, on manual
dispatch, and for every published GitHub release. It produces separate
schema-validated artifacts for the hello and Chirp-shaped workloads on:

- standard Python 3.14, where Pounce process workers are compared with
  uvicorn, Hypercorn, and Granian;
- Python 3.14t, where Pounce thread workers are compared with uvicorn and
  Hypercorn. Granian is omitted from this lane because it is a native
  benchmark-only comparison and does not define Pounce's free-threaded contract.

Each sample defaults to 120 seconds at a scheduled 1,000 requests/second, with
three repeated samples. The artifact records p50, p99, p999, errors, aggregate
RSS/CPU time series, raw driver output, versions, commands, and variance.
Workflow artifacts are retained by Actions; release-triggered artifacts are
also attached directly to the GitHub release.

## Regression Gate

The runner can compare a fresh run against a committed baseline artifact and
fail when a key metric regresses. This is the gate that would have caught a
keep-alive RPS collapse before it shipped.

```bash
# 1. Record a baseline once (repeat for stable variance), commit it.
python benchmarks/run_benchmark.py --workload chirp --workers 4 --repeat 5 \
    --artifact-output benchmarks/artifacts/<date>/chirp-baseline.json

# 2. On a later run, gate against that baseline. Exits non-zero on regression.
python benchmarks/run_benchmark.py --workload chirp --workers 4 --repeat 5 \
    --artifact-output /tmp/chirp-candidate.json \
    --compare-baseline benchmarks/artifacts/<date>/chirp-baseline.json
```

For each `(server, workload, workers)` group present in both artifacts the gate
compares **median `req_per_sec`** and **median `p99_latency_ms`**:

- It fails when median req/s drops by more than `--rps-tolerance` (default 10%)
  **or** median p99 latency rises by more than `--p99-tolerance` (default 20%).
- Groups with `sample_count < 2` in either artifact are **skipped** (snapshots,
  not regression evidence) and never fail the gate.
- Candidate groups absent from the baseline are reported but do not gate.

Use a `--repeat` of at least 2 (ideally 5) so groups carry real variance; a
single-sample run is treated as a snapshot and skipped by the gate. A
`workflow_dispatch`/cron CI job (never per-PR) can run Bengal + Chirp + hello
against committed baselines on a free-threaded build.

## Profiles

Beyond steady-state throughput, four profiles capture flagship behavior as
artifact-schema JSON:

```bash
# Sustained streaming: hold N SSE streams through the real CLI, record
# per-stream time-to-first-event, inter-event latency, and RSS/CPU over time.
python benchmarks/streaming_profile.py --streams 100 --duration 15 \
    --artifact-output benchmarks/artifacts/<date>/streaming.json

# Worker-mode comparison: drive thread and subinterpreter modes through the
# SAME Supervisor machinery and emit one variance group per mode.
python benchmarks/worker_modes.py --requests 2000 --concurrency 20 --workers 4 \
    --artifact-output benchmarks/artifacts/<date>/worker-modes.json

# Reload/drain under load: drive keep-alive /fast + in-flight /slow + /stream
# through the real CLI, fire SIGHUP then SIGTERM, and record in-flight
# completion, the 503/disconnect rate, drain duration, and orphan-worker
# absence (one variance group per worker mode).
python benchmarks/drain_profile.py --worker-mode async --workers 2 \
    --artifact-output benchmarks/artifacts/<date>/drain.json

# HTTP/3: drive persistent QUIC connections through the real CLI and record
# throughput, response latency, variance, and process telemetry.
python benchmarks/h3_profile.py --connections 4 --duration 5 --repeat 5 \
    --artifact-output benchmarks/artifacts/<date>/http3-local.json
```

All four emit `artifact-schema.json`-compatible JSON, so their output feeds the
regression gate above (each worker mode is recorded as a distinct `server`). The
drain profile's per-sample `drain` block records the four drain-contract
metrics; `clean_drain` is true only when every in-flight request completed, no
new connection was silently dropped, the process exited within the timeout, and
no worker was orphaned. The cross-worker-mode drain artifact (async / sync /
subinterpreter / process) is generated on the free-threaded 3.14t CI lane — the
sync execution path only activates in thread mode there.

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
