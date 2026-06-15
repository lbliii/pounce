#!/usr/bin/env python3
"""
Sustained-streaming benchmark profile for pounce (#141).

Holds N concurrent Server-Sent-Events (SSE) streams open through the *real*
``pounce serve`` CLI for a fixed window, then records per-stream time-to-first-
event and inter-event latency plus peak RSS / CPU% over time as an
artifact-schema-compatible JSON (``benchmarks/artifact-schema.json``).

This is the missing "sustained-streaming/SSE-under-backpressure artifact via
the real CLI" evidence. The existing ``benchmarks/test_sse_stress.py`` spawns a
``Worker`` directly and only asserts an RSS ceiling; ``chirp_events`` in
``run_benchmark.py`` only captures the first SSE event.

Usage:
    python benchmarks/streaming_profile.py --streams 100 --duration 10
    python benchmarks/streaming_profile.py --streams 50 --duration 15 \
        --artifact-output benchmarks/artifacts/<date>/streaming.json

Requires: pounce installed in editable mode (``uv sync --group dev``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import platform
import statistics
import sys
import time
from pathlib import Path

# Allow ``python benchmarks/streaming_profile.py`` as well as
# ``python -m benchmarks.streaming_profile`` by ensuring the repo root (the
# parent of this ``benchmarks/`` directory) is importable.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from benchmarks.run_benchmark import (  # noqa: E402 - path bootstrap must precede import
    _command_string,
    _TelemetrySampler,
    build_profile_artifact,
    save_artifact,
)

# The sustained-streaming app is the canonical SSE example, re-exported by
# ``benchmarks.sse_app`` (heartbeat every second, message every three).
STREAMING_APP = "benchmarks.sse_app:app"
_SSE_REQUEST = b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n"


def _server_command(port: int, workers: int) -> list[str]:
    """Build the real-CLI ``pounce serve`` command for the streaming app."""
    return [
        sys.executable,
        "-m",
        "pounce",
        "serve",
        "--app",
        STREAMING_APP,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--no-access-log",
    ]


async def _hold_stream(
    addr: tuple[str, int],
    duration: float,
) -> dict[str, float | int]:
    """Open one SSE stream, hold it for ``duration`` seconds.

    Returns per-stream metrics: number of SSE events received, time-to-first-
    event (ms), and the median inter-event latency (ms).
    """
    events = 0
    ttfb_ms: float | None = None
    inter_event_ms: list[float] = []
    started = time.perf_counter()
    last_event_at: float | None = None

    try:
        reader, writer = await asyncio.open_connection(addr[0], addr[1])
    except (ConnectionError, OSError):  # fmt: skip
        return {"events": 0, "ttfb_ms": 0.0, "inter_event_ms": 0.0, "connected": 0}

    try:
        writer.write(_SSE_REQUEST)
        await writer.drain()

        deadline = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < deadline:
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            except TimeoutError:
                continue
            if not data:
                break
            chunk_events = data.count(b"data: ")
            if chunk_events:
                now = time.perf_counter()
                if ttfb_ms is None:
                    ttfb_ms = (now - started) * 1000
                if last_event_at is not None:
                    inter_event_ms.append((now - last_event_at) * 1000)
                last_event_at = now
                events += chunk_events
    except (ConnectionError, OSError):  # fmt: skip
        pass
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await writer.wait_closed()

    return {
        "events": events,
        "ttfb_ms": round(ttfb_ms, 2) if ttfb_ms is not None else 0.0,
        "inter_event_ms": round(statistics.median(inter_event_ms), 2) if inter_event_ms else 0.0,
        "connected": 1 if events else 0,
    }


def _wait_for_port(port: int, *, timeout: float = 5.0) -> bool:
    """Wait until ``port`` accepts a TCP connection (server is ready)."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def summarize_streams(
    stream_results: list[dict[str, float | int]],
    *,
    duration: float,
) -> dict:
    """Aggregate per-stream results into a benchmark sample row.

    Builds an artifact-schema sample row: ``req_per_sec`` is the aggregate SSE
    event rate (events/sec across all held streams), ``p99_latency_ms`` is the
    p99 time-to-first-event across streams, and the median inter-event latency
    is recorded as the average latency.
    """
    connected = [r for r in stream_results if r["connected"]]
    total_events = sum(int(r["events"]) for r in stream_results)
    ttfb_values = sorted(float(r["ttfb_ms"]) for r in connected)
    inter_values = [float(r["inter_event_ms"]) for r in connected if r["inter_event_ms"]]

    def _p(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        rank = max(0, min(len(values) - 1, round(pct / 100 * (len(values) - 1))))
        return values[rank]

    return {
        "events_total": total_events,
        "streams_connected": len(connected),
        "event_rate_per_sec": round(total_events / duration, 2) if duration else 0.0,
        "ttfb_p50_ms": round(_p(ttfb_values, 50), 2),
        "ttfb_p99_ms": round(_p(ttfb_values, 99), 2),
        "inter_event_median_ms": (
            round(statistics.median(inter_values), 2) if inter_values else 0.0
        ),
    }


def run_streaming_profile(
    *,
    streams: int,
    duration: float,
    workers: int,
    port: int = 8200,
) -> dict:
    """Drive a sustained-streaming load through the real CLI and return a sample.

    Starts ``pounce serve`` as a subprocess, holds ``streams`` SSE connections
    for ``duration`` seconds while sampling RSS/CPU over time, then returns an
    artifact-schema-compatible sample row (with a nested ``streaming`` block of
    detailed per-stream stats).
    """
    import subprocess

    cmd = _server_command(port, workers)
    print(f"  Starting pounce ({STREAMING_APP}, {workers} workers)...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        if not _wait_for_port(port):
            if proc.poll() is not None:
                _, stderr = proc.communicate()
                print(f"  Server exited early: {stderr.decode()}", file=sys.stderr)
            raise RuntimeError("streaming server did not start")

        time.sleep(0.5)
        addr = ("127.0.0.1", port)

        print(f"  Holding {streams} SSE streams for {duration:.0f}s...")
        with _TelemetrySampler(proc.pid) as sampler:

            async def _run() -> list[dict[str, float | int]]:
                tasks = [asyncio.create_task(_hold_stream(addr, duration)) for _ in range(streams)]
                return await asyncio.gather(*tasks)

            stream_results = asyncio.run(_run())
        telemetry = sampler.result()
    finally:
        proc.send_signal(_sigint())
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    streaming = summarize_streams(stream_results, duration=duration)
    return {
        "server": "pounce",
        "workload": "streaming",
        "workers": workers,
        "duration_s": int(duration),
        "threads": 1,
        "connections": streams,
        # Map streaming metrics onto the schema's numeric surface so the
        # regression gate and grouped variance work against this profile.
        "req_per_sec": streaming["event_rate_per_sec"],
        "avg_latency_ms": streaming["inter_event_median_ms"],
        "p50_latency_ms": streaming["ttfb_p50_ms"],
        "p99_latency_ms": streaming["ttfb_p99_ms"],
        "transfer_per_sec": "",
        "total_requests": streaming["events_total"],
        "errors": streams - streaming["streams_connected"],
        "sample_index": 1,
        "peak_rss_bytes": telemetry.peak_rss_bytes,
        "cpu_percent_mean": telemetry.cpu_percent_mean,
        "cpu_percent_peak": telemetry.cpu_percent_peak,
        "worker_pids": telemetry.worker_pids,
        "streaming": streaming,
    }


def _sigint() -> int:
    import signal

    return int(signal.SIGINT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pounce sustained-streaming profile")
    parser.add_argument("--streams", type=int, default=100, help="Concurrent SSE streams")
    parser.add_argument("--duration", type=float, default=10.0, help="Hold duration (seconds)")
    parser.add_argument("--workers", type=int, default=1, help="Pounce worker count")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the profile N times")
    parser.add_argument("--port", type=int, default=8200, help="Server port")
    parser.add_argument(
        "--artifact-output", type=str, default=None, help="Save artifact-schema JSON"
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    print("Pounce Sustained-Streaming Profile")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")

    samples: list[dict] = []
    for sample_index in range(1, args.repeat + 1):
        print(f"\n{'=' * 60}")
        print(f"Sample {sample_index}/{args.repeat}")
        print(f"{'=' * 60}")
        sample = run_streaming_profile(
            streams=args.streams,
            duration=args.duration,
            workers=args.workers,
            port=args.port,
        )
        sample["sample_index"] = sample_index
        samples.append(sample)
        s = sample["streaming"]
        print(
            f"  events={s['events_total']} connected={s['streams_connected']}/{args.streams} "
            f"rate={s['event_rate_per_sec']}/s ttfb_p99={s['ttfb_p99_ms']}ms "
            f"inter_event={s['inter_event_median_ms']}ms"
        )

    if args.artifact_output:
        artifact = build_profile_artifact(
            profile="streaming",
            command=[sys.executable, *sys.argv],
            server_command={
                "pounce:streaming": _command_string(_server_command(args.port, args.workers))
            },
            samples=samples,
            workers=args.workers,
            duration=int(args.duration),
            connections=args.streams,
            threads=1,
            load_tool="streaming_profile.py",
            load_tool_version="in-process asyncio SSE driver",
        )
        save_artifact(artifact, Path(args.artifact_output))


if __name__ == "__main__":
    main()
