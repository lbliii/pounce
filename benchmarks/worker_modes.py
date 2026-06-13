"""
Benchmark: Thread vs Subinterpreter worker modes.

Compares throughput, latency, and memory for thread and subinterpreter worker
modes under identical conditions.  Process mode is excluded because it requires
fork(), which isn't available on all platforms.

Usage:
    python benchmarks/worker_modes.py [--requests N] [--concurrency C] [--workers W]

Example:
    python benchmarks/worker_modes.py --requests 1000 --concurrency 10 --workers 2

Requires: pounce installed in editable mode (``uv sync --group dev``).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import resource
import socket
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Allow ``python benchmarks/worker_modes.py`` as well as
# ``python -m benchmarks.worker_modes`` by ensuring the repo root (the parent
# of this ``benchmarks/`` directory) is importable.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from benchmarks.run_benchmark import (  # noqa: E402 - after path bootstrap
    _command_string,
    build_profile_artifact,
    save_artifact,
)
from pounce._runtime import WorkerMode, has_subinterpreters  # noqa: E402 - after path bootstrap
from pounce.config import ServerConfig  # noqa: E402 - after path bootstrap
from pounce.net.listener import create_listeners  # noqa: E402 - after path bootstrap
from pounce.supervisor import Supervisor  # noqa: E402 - after path bootstrap

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

APP_PATH = "examples.hello:app"


async def _hello_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app for benchmarking."""
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()
    body = b'{"message": "Hello, World!"}'
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_REQUEST = b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"


def _send_request(addr: tuple[str, int]) -> tuple[int, float]:
    """Send one HTTP request and return (status_code, latency_ms)."""
    t0 = time.perf_counter()
    with socket.create_connection(addr, timeout=5) as conn:
        conn.sendall(_REQUEST)
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
    elapsed = (time.perf_counter() - t0) * 1000  # ms

    first_line = data.split(b"\r\n")[0]
    status = int(first_line.split(b" ")[1])
    return status, elapsed


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def _get_rss_mb() -> float:
    """Current process RSS in MB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def _drive_load(
    addr: tuple[str, int], *, requests: int, concurrency: int
) -> tuple[list[float], int, float]:
    """Drive ``requests`` requests at ``concurrency`` and collect latencies.

    Returns ``(latencies_ms, errors, elapsed_s)``. Shared by every worker mode
    so the comparison is apples-to-apples.
    """
    latencies: list[float] = []
    errors = 0
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_send_request, addr) for _ in range(requests)]
        for f in as_completed(futures):
            try:
                status, lat = f.result()
                if status == 200:
                    latencies.append(lat)
                else:
                    errors += 1
            except Exception:
                errors += 1
    elapsed = time.perf_counter() - t0
    return latencies, errors, elapsed


def bench_thread_mode(*, workers: int, requests: int, concurrency: int) -> dict[str, Any]:
    """Benchmark thread worker mode driven through the Supervisor.

    Drives workers through ``Supervisor`` (not raw ``Worker`` threads) so the
    comparison against subinterpreter mode exercises the *same* spawn/health
    machinery (#141).
    """
    port = _find_free_port()
    # Note: ServerConfig.worker_mode is the *execution* mode (async/sync/...),
    # not the spawn mode. Thread spawn mode is selected via the Supervisor's
    # mode=WorkerMode.THREAD argument below.
    config = ServerConfig(
        host="127.0.0.1",
        port=port,
        workers=workers,
        access_log=False,
    )

    sockets = create_listeners(config, count=workers, shared=True)

    supervisor = Supervisor(
        config,
        app=_hello_app,
        mode=WorkerMode.THREAD,
    )
    supervisor.set_lifespan_state({})

    sup_thread = threading.Thread(target=supervisor.run, args=(sockets,), daemon=True)
    sup_thread.start()
    time.sleep(0.5)  # Let workers start

    rss_before = _get_rss_mb()
    addr = ("127.0.0.1", port)

    latencies, errors, elapsed = _drive_load(addr, requests=requests, concurrency=concurrency)
    rss_after = _get_rss_mb()

    supervisor.shutdown()
    sup_thread.join(timeout=5)
    for s in sockets:
        with contextlib.suppress(OSError):
            s.close()

    return _summarize("thread", latencies, errors, elapsed, rss_before, rss_after, workers)


def bench_subinterpreter_mode(*, workers: int, requests: int, concurrency: int) -> dict[str, Any]:
    """Benchmark subinterpreter worker mode."""
    if not has_subinterpreters():
        return {"mode": "subinterpreter", "error": "not available"}

    port = _find_free_port()
    config = ServerConfig(
        host="127.0.0.1",
        port=port,
        workers=workers,
        worker_mode="subinterpreter",
        access_log=False,
    )

    sockets = create_listeners(config, count=workers, shared=True)

    supervisor = Supervisor(
        config,
        app=None,
        mode=WorkerMode.SUBINTERPRETER,
        app_path=APP_PATH,
    )
    supervisor.set_lifespan_state({})

    sup_thread = threading.Thread(target=supervisor.run, args=(sockets,), daemon=True)
    sup_thread.start()
    time.sleep(1.0)  # Let workers start (subinterpreters need more init time)

    rss_before = _get_rss_mb()
    addr = ("127.0.0.1", port)

    latencies, errors, elapsed = _drive_load(addr, requests=requests, concurrency=concurrency)
    rss_after = _get_rss_mb()

    supervisor.shutdown()
    sup_thread.join(timeout=5)
    for s in sockets:
        with contextlib.suppress(OSError):
            s.close()

    return _summarize("subinterpreter", latencies, errors, elapsed, rss_before, rss_after, workers)


def _summarize(
    mode: str,
    latencies: list[float],
    errors: int,
    elapsed: float,
    rss_before: float,
    rss_after: float,
    workers: int,
) -> dict[str, Any]:
    if not latencies:
        return {"mode": mode, "error": "no successful requests"}

    return {
        "mode": mode,
        "workers": workers,
        "requests": len(latencies),
        "errors": errors,
        "elapsed_s": round(elapsed, 3),
        "rps": round(len(latencies) / elapsed, 1),
        "latency_avg_ms": round(statistics.mean(latencies), 2),
        "latency_p50_ms": round(statistics.median(latencies), 2),
        "latency_p99_ms": round(sorted(latencies)[int(len(latencies) * 0.99)], 2),
        "rss_before_mb": round(rss_before, 1),
        "rss_after_mb": round(rss_after, 1),
        "rss_delta_mb": round(rss_after - rss_before, 1),
    }


# ---------------------------------------------------------------------------
# Artifact emission
# ---------------------------------------------------------------------------

# Comparison-row workload label. Each worker mode becomes a "server" so the
# grouped variance and regression gate diff modes against each other.
WORKER_MODE_WORKLOAD = "worker_mode_comparison"


def _mode_result_to_sample(result: dict[str, Any], *, sample_index: int) -> dict[str, Any] | None:
    """Convert a ``_summarize`` row into an artifact-schema sample row.

    The worker mode is recorded as the ``server`` so each mode forms its own
    ``(server, workload, workers)`` variance group. Returns ``None`` for modes
    that produced no successful requests (so they are not graphed as 0 rps).
    """
    if "error" in result:
        return None
    rss_delta_bytes = int(result["rss_delta_mb"] * 1024 * 1024)
    return {
        "server": result["mode"],
        "workload": WORKER_MODE_WORKLOAD,
        "workers": result["workers"],
        "duration_s": int(result["elapsed_s"]),
        "threads": 0,
        "connections": 0,
        "req_per_sec": float(result["rps"]),
        "avg_latency_ms": float(result["latency_avg_ms"]),
        "p50_latency_ms": float(result["latency_p50_ms"]),
        "p99_latency_ms": float(result["latency_p99_ms"]),
        "transfer_per_sec": "",
        "total_requests": int(result["requests"]),
        "errors": int(result["errors"]),
        "sample_index": sample_index,
        # RSS delta (after - before) is the best in-process memory signal here;
        # surface it as server_rss_bytes for the grouped variance summary.
        "server_rss_bytes": max(0, rss_delta_bytes),
    }


def build_worker_mode_artifact(
    samples: list[dict[str, Any]],
    *,
    workers: int,
    requests: int,
    concurrency: int,
) -> dict[str, Any]:
    """Assemble an artifact-schema-compatible worker-mode comparison artifact."""
    # These commands are descriptive: this script drives each mode in-process
    # through Supervisor rather than spawning a CLI subprocess. Thread mode is
    # the free-threaded (3.14t) auto mode; subinterpreter mode is opt-in.
    base = ["python", "-m", "pounce", "serve", "--app", APP_PATH, "--workers", str(workers)]
    server_command = {
        "thread": _command_string(base),
        "subinterpreter": _command_string([*base, "--worker-mode", "subinterpreter"]),
    }
    return build_profile_artifact(
        profile=WORKER_MODE_WORKLOAD,
        command=[sys.executable, *sys.argv],
        server_command=server_command,
        samples=samples,
        workers=workers,
        duration=0,
        connections=concurrency,
        threads=0,
        load_tool="worker_modes.py",
        load_tool_version="in-process ThreadPoolExecutor driver",
        worker_mode="comparison",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _print_results(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 72)
    print("Worker Mode Benchmark Results")
    print("=" * 72)

    headers = [
        "Mode",
        "Workers",
        "Reqs",
        "Errors",
        "RPS",
        "Avg(ms)",
        "P50(ms)",
        "P99(ms)",
        "RSS Δ(MB)",
    ]
    print(f"{'  '.join(f'{h:>9}' for h in headers)}")
    print("-" * 72)

    for r in results:
        if "error" in r:
            print(f"  {r['mode']:>7}  — {r['error']}")
            continue
        print(
            f"  {r['mode']:>12}  {r['workers']:>3}  {r['requests']:>6}  "
            f"{r['errors']:>4}  {r['rps']:>8.1f}  "
            f"{r['latency_avg_ms']:>7.2f}  {r['latency_p50_ms']:>7.2f}  "
            f"{r['latency_p99_ms']:>7.2f}  {r['rss_delta_mb']:>6.1f}"
        )

    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark pounce worker modes")
    parser.add_argument("--requests", type=int, default=500, help="Total requests (default: 500)")
    parser.add_argument(
        "--concurrency", type=int, default=10, help="Concurrent connections (default: 10)"
    )
    parser.add_argument("--workers", type=int, default=2, help="Worker count (default: 2)")
    parser.add_argument(
        "--repeat", type=int, default=1, help="Repeat each mode N times (default: 1)"
    )
    parser.add_argument(
        "--artifact-output",
        type=str,
        default=None,
        help="Save artifact-schema-compatible worker-mode comparison JSON",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    print(
        f"Benchmarking: {args.requests} requests, {args.concurrency} concurrent, "
        f"{args.workers} workers"
    )
    print(f"Python: {os.sys.version}")
    print(f"Subinterpreters available: {has_subinterpreters()}")

    results: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for sample_index in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"\n=== Sample {sample_index}/{args.repeat} ===")

        print("\n→ Thread mode (via Supervisor)...")
        thread_result = bench_thread_mode(
            workers=args.workers,
            requests=args.requests,
            concurrency=args.concurrency,
        )
        results.append(thread_result)
        thread_sample = _mode_result_to_sample(thread_result, sample_index=sample_index)
        if thread_sample is not None:
            samples.append(thread_sample)

        print("→ Subinterpreter mode (via Supervisor)...")
        sub_result = bench_subinterpreter_mode(
            workers=args.workers,
            requests=args.requests,
            concurrency=args.concurrency,
        )
        results.append(sub_result)
        sub_sample = _mode_result_to_sample(sub_result, sample_index=sample_index)
        if sub_sample is not None:
            samples.append(sub_sample)

    _print_results(results)

    if args.artifact_output:
        artifact = build_worker_mode_artifact(
            samples,
            workers=args.workers,
            requests=args.requests,
            concurrency=args.concurrency,
        )
        save_artifact(artifact, Path(args.artifact_output))


# NOTE(#141): The reload/drain-under-load profile (drive steady load through
# the real CLI, send SIGHUP/SIGTERM, record active-request completion, 503/
# disconnect rate, and orphan-worker absence as an artifact) is intentionally
# deferred. It lands with the reload/drain lifecycle work in #83, which is
# still in progress and unstable on free-threaded builds. Until then, see
# tests/integration/test_signal_lifecycle.py for clean exit/recovery coverage.


if __name__ == "__main__":
    main()
