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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pounce._runtime import WorkerMode, has_subinterpreters
from pounce.config import ServerConfig
from pounce.net.listener import create_listeners
from pounce.supervisor import Supervisor
from pounce.worker import Worker

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


def bench_thread_mode(*, workers: int, requests: int, concurrency: int) -> dict[str, Any]:
    """Benchmark thread worker mode."""
    port = _find_free_port()
    config = ServerConfig(
        host="127.0.0.1",
        port=port,
        workers=workers,
        access_log=False,
    )

    sock = create_listeners(config, count=1, shared=True)[0]
    shutdown_event = threading.Event()

    # Spawn workers directly (no supervisor overhead)
    worker_threads: list[threading.Thread] = []
    for i in range(workers):
        w = Worker(
            config,
            _hello_app,
            sock,
            worker_id=i,
            shutdown_event=shutdown_event,
        )
        w.set_lifespan_state({})
        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        worker_threads.append(t)

    time.sleep(0.5)  # Let workers start

    rss_before = _get_rss_mb()
    addr = ("127.0.0.1", port)
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
    rss_after = _get_rss_mb()

    shutdown_event.set()
    for t in worker_threads:
        t.join(timeout=5)
    with contextlib.suppress(OSError):
        sock.close()

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
    args = parser.parse_args()

    print(
        f"Benchmarking: {args.requests} requests, {args.concurrency} concurrent, {args.workers} workers"
    )
    print(f"Python: {os.sys.version}")
    print(f"Subinterpreters available: {has_subinterpreters()}")

    results = []

    print("\n→ Thread mode...")
    results.append(
        bench_thread_mode(
            workers=args.workers,
            requests=args.requests,
            concurrency=args.concurrency,
        )
    )

    print("→ Subinterpreter mode...")
    results.append(
        bench_subinterpreter_mode(
            workers=args.workers,
            requests=args.requests,
            concurrency=args.concurrency,
        )
    )

    _print_results(results)


if __name__ == "__main__":
    main()
