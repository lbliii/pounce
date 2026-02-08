#!/usr/bin/env python3
"""
Memory profiling for pounce — tracks RSS over time under load.

Measures memory usage for thread workers vs process workers, idle
and under load, and reports peak/delta RSS.

Usage:
    python benchmarks/profile_memory.py
    python benchmarks/profile_memory.py --workers 4 --duration 30
    python benchmarks/profile_memory.py --tracemalloc

"""

import argparse
import asyncio
import resource
import socket
import threading
import time

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.worker import Worker

# ---------------------------------------------------------------------------
# Benchmark app
# ---------------------------------------------------------------------------

_BODY = b"Hello, World!"
_HEADERS = [
    (b"content-type", b"text/plain; charset=utf-8"),
    (b"content-length", b"13"),
]


async def _bench_app(scope: Scope, receive: Receive, send: Send) -> None:
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
    await send({"type": "http.response.start", "status": 200, "headers": _HEADERS})
    await send({"type": "http.response.body", "body": _BODY})


# ---------------------------------------------------------------------------
# RSS measurement
# ---------------------------------------------------------------------------


def get_rss_mb() -> float:
    """Get current process RSS in megabytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is in bytes on Linux, kilobytes on macOS
    import sys
    if sys.platform == "darwin":
        return usage.ru_maxrss / (1024 * 1024)
    return usage.ru_maxrss / 1024


def get_rss_bytes() -> int:
    """Get current RSS from /proc/self/status or resource module."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except FileNotFoundError:
        pass
    # macOS fallback
    usage = resource.getrusage(resource.RUSAGE_SELF)
    import sys
    if sys.platform == "darwin":
        return usage.ru_maxrss  # Already in bytes on macOS
    return usage.ru_maxrss * 1024


# ---------------------------------------------------------------------------
# Load generation
# ---------------------------------------------------------------------------

_REQUEST = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"


async def _fire_requests(
    addr: tuple[str, int],
    count: int,
    concurrency: int,
) -> None:
    """Fire HTTP requests at the given address."""
    sem = asyncio.Semaphore(concurrency)

    async def _one() -> None:
        async with sem:
            try:
                reader, writer = await asyncio.open_connection(addr[0], addr[1])
                writer.write(_REQUEST)
                await writer.drain()
                await reader.read(4096)
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    tasks = [asyncio.create_task(_one()) for _ in range(count)]
    await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Worker management
# ---------------------------------------------------------------------------


def start_workers(
    app: ASGIApp,
    count: int,
) -> tuple[threading.Event, socket.socket, list[threading.Thread], tuple[str, int]]:
    """Start multiple workers sharing a single socket."""
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    sock.setblocking(False)
    addr = sock.getsockname()

    shutdown = threading.Event()
    threads: list[threading.Thread] = []
    for i in range(count):
        worker = Worker(
            config, app, sock,
            worker_id=i,
            shutdown_event=shutdown,
        )
        t = threading.Thread(target=worker.run, daemon=True)
        threads.append(t)
        t.start()

    time.sleep(0.3)
    return shutdown, sock, threads, addr


# ---------------------------------------------------------------------------
# Memory profiling
# ---------------------------------------------------------------------------


def profile_memory(
    workers: int,
    duration: int,
    use_tracemalloc: bool,
) -> dict:
    """Profile memory usage under load."""
    import sys

    if use_tracemalloc:
        import tracemalloc
        tracemalloc.start(25)

    # Measure baseline
    baseline_rss = get_rss_bytes()

    # Start workers
    shutdown, sock, threads, addr = start_workers(_bench_app, workers)

    idle_rss = get_rss_bytes()

    # Send requests in waves, measuring RSS
    rss_samples: list[int] = []
    requests_per_wave = 200
    concurrency = 50
    waves = max(1, duration // 2)

    print(f"  Sending {waves} waves of {requests_per_wave} requests...")

    for wave in range(waves):
        asyncio.run(_fire_requests(addr, requests_per_wave, concurrency))
        current_rss = get_rss_bytes()
        rss_samples.append(current_rss)
        time.sleep(0.5)

    peak_rss = max(rss_samples) if rss_samples else idle_rss

    # Tracemalloc snapshot
    tracemalloc_top: list[str] = []
    if use_tracemalloc:
        import tracemalloc
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics("lineno")[:10]
        tracemalloc_top = [str(stat) for stat in top_stats]
        tracemalloc.stop()

    # Cleanup
    shutdown.set()
    for t in threads:
        t.join(timeout=5)
    sock.close()

    return {
        "workers": workers,
        "python_version": sys.version.split()[0],
        "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(),
        "worker_mode": "process" if getattr(sys, "_is_gil_enabled", lambda: True)() else "thread",
        "baseline_rss_mb": baseline_rss / (1024 * 1024),
        "idle_rss_mb": idle_rss / (1024 * 1024),
        "peak_rss_mb": peak_rss / (1024 * 1024),
        "delta_rss_mb": (peak_rss - baseline_rss) / (1024 * 1024),
        "rss_samples_mb": [s / (1024 * 1024) for s in rss_samples],
        "total_requests": waves * requests_per_wave,
        "tracemalloc_top": tracemalloc_top,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Pounce memory profiler")
    parser.add_argument("--workers", type=int, default=4, help="Worker count (default: 4)")
    parser.add_argument("--duration", type=int, default=10, help="Duration in seconds (default: 10)")
    parser.add_argument("--tracemalloc", action="store_true", help="Enable tracemalloc for allocation tracking")
    args = parser.parse_args()

    import sys

    print("=== Pounce Memory Profiler ===")
    print(f"  Python: {sys.version.split()[0]}")
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    print(f"  GIL:    {'enabled' if gil else 'disabled (free-threading)'}")
    print(f"  Workers: {args.workers}")
    print(f"  Mode:   {'process' if gil else 'thread'}")
    print()

    results = profile_memory(args.workers, args.duration, args.tracemalloc)

    print(f"\n  Baseline RSS: {results['baseline_rss_mb']:.1f} MB")
    print(f"  Idle RSS:     {results['idle_rss_mb']:.1f} MB")
    print(f"  Peak RSS:     {results['peak_rss_mb']:.1f} MB")
    print(f"  Delta RSS:    {results['delta_rss_mb']:.1f} MB")
    print(f"  Requests:     {results['total_requests']}")

    if results["tracemalloc_top"]:
        print("\n  Top allocations (tracemalloc):")
        for line in results["tracemalloc_top"]:
            print(f"    {line}")


if __name__ == "__main__":
    main()
