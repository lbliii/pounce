#!/usr/bin/env python3
"""
Reproducible benchmark runner for pounce.

Starts pounce (and optionally uvicorn) on separate ports, drives load
with wrk or hey, captures results as JSON, and prints a markdown summary.

Usage:
    # Quick benchmark (hello-world, 10s)
    python benchmarks/run_benchmark.py

    # Full suite with comparison
    python benchmarks/run_benchmark.py --compare --duration 30

    # Specific workload
    python benchmarks/run_benchmark.py --workload json --workers 4

    # All workloads
    python benchmarks/run_benchmark.py --workload all --compare

    # Regression gate against a committed baseline artifact
    python benchmarks/run_benchmark.py --workload chirp --repeat 5 \\
        --artifact-output candidate.json \\
        --compare-baseline benchmarks/artifacts/<date>/chirp-baseline.json

Prerequisites:
    brew install wrk    # or: go install github.com/rakyll/hey@latest

"""

import argparse
import contextlib
import json
import math
import platform
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    from benchmarks.fixed_rate import run_fixed_rate
except ModuleNotFoundError:
    # Direct ``python benchmarks/run_benchmark.py`` execution places the
    # benchmarks directory, not the repository root, on sys.path.
    from fixed_rate import run_fixed_rate

_COMMAND_ERRORS = (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired)
_LOAD_TOOL_FIND_ERRORS = (FileNotFoundError, subprocess.TimeoutExpired)
_LOAD_TOOL_VERSION_ERRORS = (FileNotFoundError, subprocess.TimeoutExpired)
_RSS_PARSE_ERRORS = (IndexError, ValueError)
_SERVER_START_RETRY_ERRORS = (ConnectionRefusedError, OSError)
_COMPARISON_ERRORS = (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired)
_PROCESS_QUERY_ERRORS = (FileNotFoundError, subprocess.TimeoutExpired)
_PROCESS_PARSE_ERRORS = (IndexError, ValueError)
_ARTIFACT_SCHEMA_PATH = Path(__file__).with_name("artifact-schema.json")


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Results from a single benchmark run."""

    server: str
    workload: str
    workers: int
    duration_s: int
    threads: int
    connections: int
    req_per_sec: float
    avg_latency_ms: float
    p50_latency_ms: float
    p99_latency_ms: float
    p999_latency_ms: float
    transfer_per_sec: str
    total_requests: int
    errors: int
    sample_index: int = 1
    server_rss_bytes: int | None = None
    peak_rss_bytes: int | None = None
    cpu_percent_mean: float | None = None
    cpu_percent_peak: float | None = None
    cpu_seconds: float | None = None
    worker_pids: list[int] = field(default_factory=list)
    telemetry_interval_seconds: float | None = None
    process_cpu_series: list[dict] = field(default_factory=list)
    load_tool_stdout: str = ""
    load_tool_stderr: str = ""


@dataclass(slots=True)
class BenchmarkSuite:
    """Collection of benchmark results."""

    timestamp: str = ""
    python_version: str = ""
    platform: str = ""
    results: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Workload definitions
# ---------------------------------------------------------------------------

WORKLOADS: dict[str, dict[str, str]] = {
    "hello": {
        "app": "benchmarks.apps.hello:app",
        "description": "Minimal hello-world (measures server overhead)",
        "method": "GET",
        "path": "/",
    },
    "json": {
        "app": "benchmarks.apps.json_app:app",
        "description": "JSON response (pre-serialized)",
        "method": "GET",
        "path": "/",
    },
    "echo": {
        "app": "benchmarks.apps.echo:app",
        "description": "POST body echo (1KB payload)",
        "method": "POST",
        "path": "/",
        "body_size": "1024",
    },
    "bengal": {
        "app": "benchmarks.apps.bengal_static:app",
        "description": "Bengal-shaped generated static site home page",
        "method": "GET",
        "path": "/",
    },
    "bengal_asset": {
        "app": "benchmarks.apps.bengal_static:app",
        "description": "Bengal-shaped generated static site CSS asset",
        "method": "GET",
        "path": "/assets/site.css",
    },
    "bengal_feed": {
        "app": "benchmarks.apps.bengal_static:app",
        "description": "Bengal-shaped generated static site XML feed",
        "method": "GET",
        "path": "/feed.xml",
    },
    "bengal_post": {
        "app": "benchmarks.apps.bengal_static:app",
        "description": "Bengal-shaped generated static site post page",
        "method": "GET",
        "path": "/posts/launch/",
    },
    "chirp": {
        "app": "benchmarks.apps.chirp_forum:app",
        "description": "Chirp/LB Sonic-shaped multi-tenant forum thread",
        "method": "GET",
        "path": "/threads/1",
    },
    "chirp_asset": {
        "app": "benchmarks.apps.chirp_forum:app",
        "description": "Chirp/LB Sonic-shaped forum CSS asset",
        "method": "GET",
        "path": "/assets/forum.css",
    },
    "chirp_events": {
        "app": "benchmarks.apps.chirp_forum:app",
        "description": "Chirp/LB Sonic-shaped forum SSE first event",
        "method": "GET",
        "path": "/events",
    },
    "chirp_home": {
        "app": "benchmarks.apps.chirp_forum:app",
        "description": "Chirp/LB Sonic-shaped multi-tenant forum home",
        "method": "GET",
        "path": "/",
    },
}


def _benchmark_url(port: int, workload: str) -> str:
    """Return the URL that exercises the configured workload path."""
    path = WORKLOADS[workload].get("path", "/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"http://127.0.0.1:{port}{path}"


def _server_command(server: str, workload: str, port: int, workers: int) -> list[str]:
    """Build the server command used for a benchmark run."""
    wl = WORKLOADS[workload]
    if server == "pounce":
        return [
            sys.executable,
            "-m",
            "pounce",
            "serve",
            "--app",
            wl["app"],
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--no-access-log",
            "--no-compression",
        ]
    if server == "uvicorn":
        return [
            sys.executable,
            "-m",
            "uvicorn",
            wl["app"],
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--no-access-log",
        ]
    if server == "hypercorn":
        return [
            sys.executable,
            "-m",
            "hypercorn",
            "--bind",
            f"127.0.0.1:{port}",
            "--workers",
            str(workers),
            wl["app"],
        ]
    if server == "granian":
        return [
            sys.executable,
            "-m",
            "granian",
            "--interface",
            "asgi",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            str(workers),
            wl["app"],
        ]
    msg = f"unknown benchmark server: {server}"
    raise ValueError(msg)


def _command_string(command: list[str]) -> str:
    """Render a command list for artifact metadata."""
    rendered = list(command)
    if rendered:
        executable = Path(rendered[0]).name
        if executable.startswith("python"):
            rendered[0] = executable
    return " ".join(rendered)


def _sample_plan(workloads: list[str], repeat: int) -> list[tuple[int, str]]:
    """Return the ordered benchmark samples to run."""
    if repeat < 1:
        msg = "repeat must be >= 1"
        raise ValueError(msg)
    return [
        (sample_index, workload) for sample_index in range(1, repeat + 1) for workload in workloads
    ]


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------


def _start_server(
    cmd: list[str],
    port: int,
    *,
    timeout: float = 5.0,
) -> subprocess.Popen:
    """Start a server process and wait for it to be ready."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for the port to accept connections
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return proc
        except _SERVER_START_RETRY_ERRORS:
            time.sleep(0.2)
            if proc.poll() is not None:
                _, stderr = proc.communicate()
                print(f"  Server exited prematurely: {stderr.decode()}", file=sys.stderr)
                raise RuntimeError("Server failed to start") from None

    proc.terminate()
    raise RuntimeError(f"Server did not start within {timeout}s")


def _stop_server(proc: subprocess.Popen) -> None:
    """Gracefully stop a server process."""
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _process_rss_bytes(proc: subprocess.Popen) -> int | None:
    """Return current process RSS in bytes when the platform exposes it."""
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(proc.pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except _COMMAND_ERRORS:
        return None
    output = result.stdout.strip()
    if not output:
        return None
    try:
        rss_kib = int(output.split()[0])
    except _RSS_PARSE_ERRORS:
        return None
    return rss_kib * 1024


# ---------------------------------------------------------------------------
# Process telemetry (peak RSS + CPU, incl. child/worker processes)
# ---------------------------------------------------------------------------


def _child_pids_proc(pid: int) -> list[int]:
    """Discover direct child pids via /proc (Linux), best-effort."""
    children: list[int] = []
    try:
        tasks = Path(f"/proc/{pid}/task")
        for task_dir in tasks.iterdir():
            child_file = task_dir / "children"
            try:
                raw = child_file.read_text()
            except OSError:
                continue
            children.extend(int(tok) for tok in raw.split() if tok.isdigit())
    except OSError:
        return []
    return children


def _child_pids_ps(pid: int) -> list[int]:
    """Discover descendant pids via ``ps`` (macOS/BSD/Linux), best-effort."""
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except _PROCESS_QUERY_ERRORS:
        return []
    parent_to_children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            child, parent = int(parts[0]), int(parts[1])
        except _PROCESS_PARSE_ERRORS:
            continue
        parent_to_children.setdefault(parent, []).append(child)
    # Walk the tree to collect every descendant of ``pid``.
    descendants: list[int] = []
    stack = list(parent_to_children.get(pid, []))
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        descendants.append(current)
        stack.extend(parent_to_children.get(current, []))
    return descendants


def _process_tree_pids(pid: int) -> list[int]:
    """Return ``pid`` plus its descendant worker pids (best-effort, dedup)."""
    children = _child_pids_proc(pid) or _child_pids_ps(pid)
    ordered: list[int] = [pid]
    for child in children:
        if child not in ordered:
            ordered.append(child)
    return ordered


def _sample_process_stats(pids: list[int]) -> dict[int, dict[str, float]]:
    """Return per-pid {rss_bytes, cpu_percent} via a single ``ps`` call.

    Cross-platform best-effort: returns an empty mapping if ``ps`` is
    unavailable or yields nothing parseable.
    """
    if not pids:
        return {}
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,rss=,%cpu=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except _PROCESS_QUERY_ERRORS:
        return {}
    stats: dict[int, dict[str, float]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            sampled_pid = int(parts[0])
            rss_kib = int(parts[1])
            cpu_percent = float(parts[2])
        except _PROCESS_PARSE_ERRORS:
            continue
        stats[sampled_pid] = {
            "rss_bytes": float(rss_kib * 1024),
            "cpu_percent": cpu_percent,
        }
    return stats


@dataclass(slots=True)
class _ProcessTelemetry:
    """Aggregated under-load telemetry for a server process tree."""

    peak_rss_bytes: int | None = None
    cpu_percent_mean: float | None = None
    cpu_percent_peak: float | None = None
    worker_pids: list[int] = field(default_factory=list)
    interval_seconds: float | None = None
    process_cpu_series: list[dict] = field(default_factory=list)
    sample_count: int = 0
    supported: bool = False


class _TelemetrySampler:
    """Background poller for peak RSS and CPU% of a process tree.

    Polls ``ps`` for the root server pid and every descendant worker pid at a
    fixed interval. Records the per-process CPU/RSS series plus peak aggregate
    RSS and aggregate CPU% (mean and peak). Degrades gracefully: if no process
    stats can be read it reports ``supported=False`` and an empty series.
    """

    def __init__(self, pid: int, *, interval: float = 0.2) -> None:
        self._pid = pid
        self._interval = max(0.01, interval)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bench-telemetry", daemon=True)
        self._started_ns = time.monotonic_ns()
        self._peak_rss = 0
        self._cpu_samples: list[float] = []
        self._process_cpu_series: list[dict] = []
        self._worker_pids: set[int] = set()
        self._any_sample = False

    def _poll_once(self) -> None:
        pids = _process_tree_pids(self._pid)
        stats = _sample_process_stats(pids)
        if not stats:
            return
        self._any_sample = True
        self._worker_pids.update(stats.keys())
        total_rss = int(sum(entry["rss_bytes"] for entry in stats.values()))
        total_cpu = sum(entry["cpu_percent"] for entry in stats.values())
        self._peak_rss = max(self._peak_rss, total_rss)
        self._cpu_samples.append(total_cpu)
        self._process_cpu_series.append(
            {
                "elapsed_seconds": round((time.monotonic_ns() - self._started_ns) / 1e9, 3),
                "rss_bytes_total": total_rss,
                "cpu_percent_total": round(total_cpu, 2),
                "processes": [
                    {
                        "pid": sampled_pid,
                        "role": "root" if sampled_pid == self._pid else "child",
                        "rss_bytes": int(entry["rss_bytes"]),
                        "cpu_percent": round(entry["cpu_percent"], 2),
                    }
                    for sampled_pid, entry in sorted(stats.items())
                ],
            }
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            # Telemetry must never crash a benchmark run.
            with contextlib.suppress(Exception):
                self._poll_once()
            self._stop.wait(self._interval)

    def __enter__(self) -> _TelemetrySampler:
        # Take one synchronous sample so very short runs still capture data.
        self._poll_once()
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        # Final sample for the tail of the load window.
        with contextlib.suppress(Exception):
            self._poll_once()

    def result(self) -> _ProcessTelemetry:
        if not self._any_sample:
            return _ProcessTelemetry(supported=False)
        cpu_mean = statistics.fmean(self._cpu_samples) if self._cpu_samples else None
        cpu_peak = max(self._cpu_samples) if self._cpu_samples else None
        return _ProcessTelemetry(
            peak_rss_bytes=self._peak_rss or None,
            cpu_percent_mean=round(cpu_mean, 2) if cpu_mean is not None else None,
            cpu_percent_peak=round(cpu_peak, 2) if cpu_peak is not None else None,
            worker_pids=sorted(self._worker_pids),
            interval_seconds=self._interval,
            process_cpu_series=list(self._process_cpu_series),
            sample_count=len(self._cpu_samples),
            supported=True,
        )


# ---------------------------------------------------------------------------
# wrk / hey detection and execution
# ---------------------------------------------------------------------------


def _find_load_tool() -> str:
    """Find wrk or hey on PATH."""
    for tool in ("wrk", "hey"):
        try:
            subprocess.run(
                [tool, "--help"],
                capture_output=True,
                timeout=5,
            )
            return tool
        except _LOAD_TOOL_FIND_ERRORS:
            continue
    print(
        "Error: Neither 'wrk' nor 'hey' found on PATH.\n"
        "Install with: brew install wrk\n"
        "          or: go install github.com/rakyll/hey@latest",
        file=sys.stderr,
    )
    sys.exit(1)


def _load_tool_version(tool: str) -> str:
    """Return best-effort load tool version metadata."""
    if tool == "pounce-fixed-rate":
        return "builtin-v1"
    for flag in ("--version", "-version", "--help"):
        try:
            result = subprocess.run(
                [tool, flag],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except _LOAD_TOOL_VERSION_ERRORS:
            continue
        output = (result.stdout or result.stderr).strip().splitlines()
        if output:
            return output[0]
    return "unknown"


def _run_wrk(
    url: str,
    *,
    duration: int,
    threads: int,
    connections: int,
    method: str = "GET",
    body_size: str | None = None,
) -> dict:
    """Run wrk and parse the output."""
    cmd = [
        "wrk",
        f"-t{threads}",
        f"-c{connections}",
        f"-d{duration}s",
        "--latency",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)
    parsed = _parse_wrk_output(result.stdout)
    parsed["load_tool_stdout"] = result.stdout
    parsed["load_tool_stderr"] = result.stderr
    return parsed


def _run_hey(
    url: str,
    *,
    duration: int,
    threads: int,
    connections: int,
    method: str = "GET",
    body_size: str | None = None,
) -> dict:
    """Run hey and parse the output."""
    cmd = [
        "hey",
        "-z",
        f"{duration}s",
        "-c",
        str(connections),
        "-m",
        method,
        url,
    ]
    if body_size and method == "POST":
        # Generate a body of the specified size
        cmd.extend(["-d", "x" * int(body_size)])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)
    parsed = _parse_hey_output(result.stdout)
    parsed["load_tool_stdout"] = result.stdout
    parsed["load_tool_stderr"] = result.stderr
    return parsed


def _parse_wrk_output(output: str) -> dict:
    """Parse wrk --latency output into a result dict."""
    result: dict = {
        "req_per_sec": 0.0,
        "avg_latency_ms": 0.0,
        "p50_latency_ms": 0.0,
        "p99_latency_ms": 0.0,
        "p999_latency_ms": 0.0,
        "transfer_per_sec": "",
        "total_requests": 0,
        "errors": 0,
    }

    for line in output.splitlines():
        line = line.strip()

        # Requests/sec
        if line.startswith("Requests/sec:"):
            result["req_per_sec"] = float(line.split(":")[1].strip())

        # Avg latency
        m = re.match(r"Latency\s+([\d.]+)(us|ms|s)", line)
        if m:
            val, unit = float(m.group(1)), m.group(2)
            if unit == "us":
                val /= 1000
            elif unit == "s":
                val *= 1000
            result["avg_latency_ms"] = val

        # Percentile latencies
        if line.startswith("50%"):
            result["p50_latency_ms"] = _parse_wrk_latency(line)
        if line.startswith("99%"):
            result["p99_latency_ms"] = _parse_wrk_latency(line)

        # Transfer
        if line.startswith("Transfer/sec:"):
            result["transfer_per_sec"] = line.split(":")[1].strip()

        # Total requests
        m = re.match(r"(\d+)\s+requests in", line)
        if m:
            result["total_requests"] = int(m.group(1))

        # Errors
        if "Socket errors" in line:
            numbers = re.findall(r"\d+", line)
            result["errors"] = sum(int(n) for n in numbers)

    return result


def _parse_wrk_latency(line: str) -> float:
    """Parse a wrk percentile latency line like '50%   1.23ms'."""
    parts = line.split()
    if len(parts) >= 2:
        val_str = parts[1]
        m = re.match(r"([\d.]+)(us|ms|s)", val_str)
        if m:
            val, unit = float(m.group(1)), m.group(2)
            if unit == "us":
                return val / 1000
            if unit == "s":
                return val * 1000
            return val
    return 0.0


def _parse_hey_output(output: str) -> dict:
    """Parse hey output into a result dict."""
    result: dict = {
        "req_per_sec": 0.0,
        "avg_latency_ms": 0.0,
        "p50_latency_ms": 0.0,
        "p99_latency_ms": 0.0,
        "p999_latency_ms": 0.0,
        "transfer_per_sec": "",
        "total_requests": 0,
        "errors": 0,
    }

    for line in output.splitlines():
        line = line.strip()

        if line.startswith("Requests/sec:"):
            result["req_per_sec"] = float(line.split(":")[1].strip())

        if line.startswith("Average:"):
            val = float(line.split(":")[1].strip().removesuffix("secs").strip())
            result["avg_latency_ms"] = val * 1000

        if "50%" in line and "in" not in line:
            parts = line.split()
            if len(parts) >= 2:
                result["p50_latency_ms"] = float(parts[1]) * 1000

        if "99%" in line and "in" not in line:
            parts = line.split()
            if len(parts) >= 2:
                result["p99_latency_ms"] = float(parts[1]) * 1000

        m = re.match(r"\s*(\d+) responses", line)
        if m:
            result["total_requests"] = int(m.group(1))

        if "Status code distribution" not in line and "[200]" in line:
            m2 = re.search(r"\[200\]\s+(\d+)", line)
            if m2:
                result["total_requests"] = int(m2.group(1))

        if "Error distribution" in line:
            result["errors"] = 1  # Mark that errors exist

    return result


# ---------------------------------------------------------------------------
# Benchmark execution
# ---------------------------------------------------------------------------


def run_benchmark(
    *,
    workload: str,
    workers: int,
    duration: int,
    threads: int,
    connections: int,
    compare: bool,
    servers: tuple[str, ...] | None = None,
    load_tool: str | None = None,
    rate: int | None = None,
    port: int = 8100,
    sample_index: int = 1,
) -> list[BenchmarkResult]:
    """Run one workload against Pounce and selected comparison servers."""
    wl = WORKLOADS[workload]
    selected_servers = servers or (("pounce", "uvicorn") if compare else ("pounce",))
    if "pounce" not in selected_servers:
        raise ValueError("servers must include pounce")
    tool = load_tool or ("pounce-fixed-rate" if rate is not None else _find_load_tool())
    if tool == "pounce-fixed-rate" and (rate is None or rate < 1):
        raise ValueError("fixed-rate load requires rate >= 1")
    results: list[BenchmarkResult] = []

    method = wl.get("method", "GET")
    body_size = wl.get("body_size")

    def drive(url: str) -> dict:
        if tool == "pounce-fixed-rate":
            return run_fixed_rate(
                url,
                duration=duration,
                connections=connections,
                rate=rate or 0,
                method=method,
                body_size=body_size,
            )
        runner = _run_wrk if tool == "wrk" else _run_hey
        return runner(
            url,
            duration=duration,
            threads=threads,
            connections=connections,
            method=method,
            body_size=body_size,
        )

    for server_index, server_name in enumerate(selected_servers):
        server_port = port + server_index
        print(f"\n  Starting {server_name} ({workload}, {workers} workers)...")
        command = _server_command(server_name, workload, server_port, workers)
        process: subprocess.Popen | None = None
        try:
            process = _start_server(command, server_port)
            time.sleep(0.5)
            print(f"  Running {tool} ({duration}s, {connections} connections)...")
            with _TelemetrySampler(process.pid) as sampler:
                raw = drive(_benchmark_url(server_port, workload))
            telemetry = sampler.result()
            server_rss_bytes = _process_rss_bytes(process)
            results.append(
                BenchmarkResult(
                    server=server_name,
                    workload=workload,
                    workers=workers,
                    duration_s=duration,
                    threads=threads,
                    connections=connections,
                    sample_index=sample_index,
                    server_rss_bytes=server_rss_bytes,
                    peak_rss_bytes=telemetry.peak_rss_bytes,
                    cpu_percent_mean=telemetry.cpu_percent_mean,
                    cpu_percent_peak=telemetry.cpu_percent_peak,
                    worker_pids=telemetry.worker_pids,
                    telemetry_interval_seconds=telemetry.interval_seconds,
                    process_cpu_series=telemetry.process_cpu_series,
                    **raw,
                )
            )
        except _COMPARISON_ERRORS as exc:
            if server_name == "pounce":
                raise
            print(f"  {server_name} comparison skipped: {exc}", file=sys.stderr)
        finally:
            if process is not None and process.poll() is None:
                _stop_server(process)

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_markdown_table(results: list[BenchmarkResult]) -> None:
    """Print results as a markdown table."""
    print("\n## Benchmark Results\n")
    print(
        "| Server | Workload | Workers | Req/s | Avg (ms) | p50 (ms) | p99 (ms) | "
        "p999 (ms) | Errors | Peak RSS (MB) | CPU% |"
    )
    print(
        "|--------|----------|---------|-------|----------|----------|----------|"
        "-----------|--------|---------------|------|"
    )
    for r in results:
        peak_rss = f"{r.peak_rss_bytes / 1_048_576:.1f}" if r.peak_rss_bytes is not None else "n/a"
        cpu = f"{r.cpu_percent_peak:.0f}" if r.cpu_percent_peak is not None else "n/a"
        print(
            f"| {r.server} | {r.workload} | {r.workers} | "
            f"{r.req_per_sec:,.0f} | {r.avg_latency_ms:.2f} | "
            f"{r.p50_latency_ms:.2f} | {r.p99_latency_ms:.2f} | "
            f"{r.p999_latency_ms:.2f} | {r.errors} | "
            f"{peak_rss} | {cpu} |"
        )

    # Print Pounce ratios against every selected comparison server.
    pounce_results = [r for r in results if r.server == "pounce"]
    comparison_results = [r for r in results if r.server != "pounce"]

    if pounce_results and comparison_results:
        print("\n### Throughput Ratios\n")
        for p in pounce_results:
            for comparison in comparison_results:
                if p.workload == comparison.workload and p.workers == comparison.workers:
                    ratio = (
                        p.req_per_sec / comparison.req_per_sec if comparison.req_per_sec > 0 else 0
                    )
                    print(
                        f"- pounce / {comparison.server}, {p.workload} "
                        f"({p.workers}w): **{ratio:.2f}x**"
                    )


def save_json(suite: BenchmarkSuite, path: Path) -> None:
    """Save benchmark results to a JSON file."""
    path.write_text(json.dumps(asdict(suite), indent=2) + "\n")
    print(f"\nResults saved to {path}")


def _git_sha() -> str:
    """Return the current git SHA if available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except _COMMAND_ERRORS:
        return "unknown"
    return result.stdout.strip()


def _python_gil_mode() -> str:
    """Return whether this Python build currently has the GIL enabled."""
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    if callable(is_gil_enabled):
        return "gil-enabled" if is_gil_enabled() else "free-threaded"
    return "unknown"


def _server_version(module: str) -> str:
    """Return best-effort comparison server version metadata."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", module, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except _COMMAND_ERRORS:
        return "unknown"
    output = (result.stdout or result.stderr).strip().splitlines()
    if output:
        return output[0]
    return "unknown"


def _artifact_samples(results: list[dict]) -> list[dict]:
    """Return compact sample rows without verbose raw streams/telemetry series."""
    raw_fields = {"load_tool_stdout", "load_tool_stderr", "process_cpu_series"}
    return [{key: value for key, value in row.items() if key not in raw_fields} for row in results]


def _raw_output_entries(results: list[dict], load_tool: str) -> list[dict]:
    """Return raw load-tool output entries for artifact evidence."""
    return [
        {
            "server": row.get("server", "unknown"),
            "workload": row.get("workload", "unknown"),
            "workers": row.get("workers", 0),
            "sample_index": row.get("sample_index", 1),
            "load_tool": load_tool,
            "stdout": row.get("load_tool_stdout", ""),
            "stderr": row.get("load_tool_stderr", ""),
        }
        for row in results
    ]


def _nearest_rank(values: list[float], percentile: int) -> float:
    """Return a nearest-rank percentile for repeated benchmark samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _metric_summary(rows: list[dict], metric: str) -> dict:
    """Summarize a numeric benchmark metric across repeated samples."""
    values = [float(row.get(metric, 0.0)) for row in rows]
    if not values:
        return {
            "min": 0.0,
            "median": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "variance": 0.0,
        }
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p95": _nearest_rank(values, 95),
        "max": max(values),
        "variance": statistics.pvariance(values) if len(values) > 1 else 0.0,
    }


def _group_sample_summaries(samples: list[dict]) -> list[dict]:
    """Group benchmark samples into artifact-ready summaries."""
    groups: dict[tuple[str, str, int], list[dict]] = {}
    for sample in samples:
        key = (
            str(sample.get("server", "unknown")),
            str(sample.get("workload", "unknown")),
            int(sample.get("workers", 0)),
        )
        groups.setdefault(key, []).append(sample)

    summaries = []
    for (server, workload, workers), rows in sorted(groups.items()):
        rss_rows = [row for row in rows if row.get("server_rss_bytes") is not None]
        peak_rows = [row for row in rows if row.get("peak_rss_bytes") is not None]
        cpu_rows = [row for row in rows if row.get("cpu_percent_peak") is not None]
        worker_pids = sorted({pid for row in rows for pid in (row.get("worker_pids") or [])})
        summaries.append(
            {
                "server": server,
                "workload": workload,
                "workers": workers,
                "sample_count": len(rows),
                "req_per_sec": _metric_summary(rows, "req_per_sec"),
                "p99_latency_ms": _metric_summary(rows, "p99_latency_ms"),
                "p999_latency_ms": _metric_summary(rows, "p999_latency_ms"),
                "server_rss_bytes": _metric_summary(rss_rows, "server_rss_bytes")
                if rss_rows
                else None,
                "peak_rss_bytes": _metric_summary(peak_rows, "peak_rss_bytes")
                if peak_rows
                else None,
                "cpu_percent": _metric_summary(cpu_rows, "cpu_percent_peak") if cpu_rows else None,
                "worker_pids": worker_pids,
                "errors_total": sum(int(row.get("errors", 0)) for row in rows),
            }
        )
    return summaries


def _telemetry_block(samples: list[dict]) -> dict:
    """Summarize per-process telemetry captured under load.

    Reports whether the platform exposed process telemetry, the aggregate
    peak RSS (summed across the supervisor and any forked worker processes),
    CPU% (mean of per-sample peaks, and the maximum observed), CPU-seconds
    when available, the union of observed worker pids, and the per-process
    time series for each benchmark sample.
    """
    peak_values = [
        float(row["peak_rss_bytes"]) for row in samples if row.get("peak_rss_bytes") is not None
    ]
    cpu_peak_values = [
        float(row["cpu_percent_peak"]) for row in samples if row.get("cpu_percent_peak") is not None
    ]
    cpu_mean_values = [
        float(row["cpu_percent_mean"]) for row in samples if row.get("cpu_percent_mean") is not None
    ]
    cpu_seconds_values = [
        float(row["cpu_seconds"]) for row in samples if row.get("cpu_seconds") is not None
    ]
    worker_pids = sorted({pid for row in samples for pid in (row.get("worker_pids") or [])})
    process_cpu_series = [
        {
            "server": row.get("server", "unknown"),
            "workload": row.get("workload", "unknown"),
            "workers": int(row.get("workers", 0)),
            "sample_index": int(row.get("sample_index", 1)),
            "interval_seconds": row.get("telemetry_interval_seconds"),
            "points": row.get("process_cpu_series", []),
        }
        for row in samples
        if "process_cpu_series" in row
    ]
    return {
        "supported": bool(peak_values or cpu_peak_values),
        "peak_rss_bytes": int(max(peak_values)) if peak_values else None,
        "cpu_percent": {
            "mean": round(statistics.fmean(cpu_mean_values), 2) if cpu_mean_values else None,
            "peak": round(max(cpu_peak_values), 2) if cpu_peak_values else None,
        },
        "cpu_seconds": round(max(cpu_seconds_values), 3) if cpu_seconds_values else None,
        "worker_pids": worker_pids,
        "process_cpu_series": process_cpu_series,
        "note": (
            "peak_rss_bytes is the max under-load RSS summed across the supervisor and "
            "forked worker processes; cpu_percent aggregates the whole process tree. "
            "process_cpu_series preserves each ps observation by root/child pid and "
            "elapsed sample time. Null summaries and empty point lists mean the platform "
            "did not expose process telemetry."
        ),
    }


def build_artifact(
    suite: BenchmarkSuite,
    *,
    command: list[str],
    workload: str,
    workers: int,
    duration: int,
    connections: int,
    threads: int,
    load_tool: str,
    load_tool_version: str,
    compare: bool,
    servers: tuple[str, ...] | None = None,
    target_rps: int | None = None,
    port: int = 8100,
) -> dict:
    """Build JSON metadata matching benchmarks/artifact-schema.json."""
    created_at = suite.timestamp or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    samples = _artifact_samples(suite.results)
    summaries = _group_sample_summaries(samples)
    telemetry = _telemetry_block(suite.results)
    server_commands: dict[str, str] = {}
    selected_servers = servers or (("pounce", "uvicorn") if compare else ("pounce",))
    comparison_servers = [server for server in selected_servers if server != "pounce"]
    if len(comparison_servers) == 1:
        comparison_target: str | list[str] | None = comparison_servers[0]
        comparison_version: str | dict[str, str] | None = _server_version(comparison_servers[0])
    elif comparison_servers:
        comparison_target = comparison_servers
        comparison_version = {
            server_name: _server_version(server_name) for server_name in comparison_servers
        }
    else:
        comparison_target = None
        comparison_version = None
    workloads = list(WORKLOADS) if workload == "all" else [workload]
    for wl in workloads:
        for server_index, server_name in enumerate(selected_servers):
            server_commands[f"{server_name}:{wl}"] = _command_string(
                _server_command(server_name, wl, port + server_index, workers)
            )

    return {
        "artifact_schema_version": 2,
        "artifact_id": f"pounce-{workload}-{created_at.replace(':', '').replace('+', '-')}",
        "created_at": created_at,
        "git_sha": _git_sha(),
        "command": _command_string(command),
        "server_command": server_commands,
        "workload": workload,
        "python_version": suite.python_version or sys.version,
        "python_gil_mode": _python_gil_mode(),
        "os": suite.platform or platform.platform(),
        "hardware": f"{platform.machine()} {platform.processor()}".strip(),
        "worker_mode": "auto",
        "workers": workers,
        "duration_seconds": duration,
        "connections": connections,
        "threads": threads,
        "target_rps": target_rps,
        "load_tool": load_tool,
        "load_tool_version": load_tool_version,
        "comparison_target": comparison_target,
        "comparison_target_version": comparison_version,
        "samples": samples,
        "telemetry": telemetry,
        "variance": {
            "sample_count": len(samples),
            "groups": summaries,
            "note": "sample groups with sample_count < 2 are snapshots, not regression evidence",
        },
        "raw_output": _raw_output_entries(suite.results, load_tool),
        "summary": {
            "groups": summaries,
            "results": samples,
        },
    }


def build_profile_artifact(
    *,
    profile: str,
    command: list[str],
    server_command: dict[str, str],
    samples: list[dict],
    workers: int,
    duration: int,
    connections: int,
    threads: int,
    load_tool: str,
    load_tool_version: str,
    worker_mode: str = "auto",
    comparison_target: str | None = None,
    comparison_target_version: str | None = None,
    raw_output: list[dict] | None = None,
    timestamp: str = "",
    python_version: str = "",
    os_name: str = "",
    extra: dict | None = None,
) -> dict:
    """Assemble an artifact-schema-compatible dict for a custom profile.

    Unlike :func:`build_artifact` (which is wired to the workload runner), this
    builds the same schema from pre-computed sample rows so that
    in-process profiles — sustained streaming, worker-mode comparison — emit
    governed artifacts too. Each sample row should carry at least ``server``,
    ``workload``, ``workers``, ``req_per_sec``, and ``p99_latency_ms`` so the
    grouped variance and regression gate work against it.
    """
    created_at = timestamp or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    clean_samples = _artifact_samples(samples)
    summaries = _group_sample_summaries(clean_samples)
    telemetry = _telemetry_block(samples)
    artifact = {
        "artifact_schema_version": 2,
        "artifact_id": f"pounce-{profile}-{created_at.replace(':', '').replace('+', '-')}",
        "created_at": created_at,
        "git_sha": _git_sha(),
        "command": _command_string(command),
        "server_command": server_command,
        "workload": profile,
        "python_version": python_version or sys.version,
        "python_gil_mode": _python_gil_mode(),
        "os": os_name or platform.platform(),
        "hardware": f"{platform.machine()} {platform.processor()}".strip(),
        "worker_mode": worker_mode,
        "workers": workers,
        "duration_seconds": duration,
        "connections": connections,
        "threads": threads,
        "load_tool": load_tool,
        "load_tool_version": load_tool_version,
        "comparison_target": comparison_target,
        "comparison_target_version": comparison_target_version,
        "samples": clean_samples,
        "telemetry": telemetry,
        "variance": {
            "sample_count": len(clean_samples),
            "groups": summaries,
            "note": "sample groups with sample_count < 2 are snapshots, not regression evidence",
        },
        "raw_output": raw_output if raw_output is not None else [],
        "summary": {
            "groups": summaries,
            "results": clean_samples,
        },
    }
    if extra:
        artifact.update(extra)
    return artifact


def _require_fields(value: dict, fields: list[str], *, context: str) -> None:
    missing = sorted(set(fields) - set(value))
    if missing:
        msg = f"{context} missing required fields: {', '.join(missing)}"
        raise ValueError(msg)


def validate_artifact(artifact: dict) -> None:
    """Validate artifact metadata and nested process-series shape."""
    schema = json.loads(_ARTIFACT_SCHEMA_PATH.read_text())
    _require_fields(artifact, schema["required_fields"], context="benchmark artifact")
    if artifact["artifact_schema_version"] != schema["version"]:
        msg = (
            "benchmark artifact schema version "
            f"{artifact['artifact_schema_version']} does not match {schema['version']}"
        )
        raise ValueError(msg)

    telemetry = artifact["telemetry"]
    if not isinstance(telemetry, dict):
        raise ValueError("benchmark artifact telemetry must be an object")
    telemetry_schema = schema["telemetry"]
    _require_fields(
        telemetry,
        telemetry_schema["required_fields"],
        context="benchmark artifact telemetry",
    )
    series = telemetry["process_cpu_series"]
    if not isinstance(series, list):
        raise ValueError("benchmark artifact process_cpu_series must be a list")
    series_schema = telemetry_schema["process_cpu_series"]
    for series_index, entry in enumerate(series):
        if not isinstance(entry, dict):
            raise ValueError(f"process_cpu_series[{series_index}] must be an object")
        _require_fields(
            entry,
            series_schema["entry_required_fields"],
            context=f"process_cpu_series[{series_index}]",
        )
        points = entry["points"]
        if not isinstance(points, list):
            raise ValueError(f"process_cpu_series[{series_index}].points must be a list")
        for point_index, point in enumerate(points):
            if not isinstance(point, dict):
                raise ValueError(
                    f"process_cpu_series[{series_index}].points[{point_index}] must be an object"
                )
            _require_fields(
                point,
                series_schema["point_required_fields"],
                context=f"process_cpu_series[{series_index}].points[{point_index}]",
            )
            processes = point["processes"]
            if not isinstance(processes, list):
                raise ValueError(
                    f"process_cpu_series[{series_index}].points[{point_index}].processes "
                    "must be a list"
                )
            for process_index, process in enumerate(processes):
                if not isinstance(process, dict):
                    raise ValueError(
                        f"process_cpu_series[{series_index}].points[{point_index}]"
                        f".processes[{process_index}] must be an object"
                    )
                _require_fields(
                    process,
                    series_schema["process_required_fields"],
                    context=(
                        f"process_cpu_series[{series_index}].points[{point_index}]"
                        f".processes[{process_index}]"
                    ),
                )


def save_artifact(artifact: dict, path: Path) -> None:
    """Validate and save a benchmark artifact JSON file."""
    validate_artifact(artifact)
    path.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"\nBenchmark artifact saved to {path}")


# ---------------------------------------------------------------------------
# Regression gate (baseline comparison)
# ---------------------------------------------------------------------------

# Default tolerances for the regression gate. A run fails when median req/s
# drops by more than ``DEFAULT_RPS_TOLERANCE`` *or* median p99 latency rises by
# more than ``DEFAULT_P99_TOLERANCE`` relative to the committed baseline.
DEFAULT_RPS_TOLERANCE = 0.10  # 10% throughput drop
DEFAULT_P99_TOLERANCE = 0.20  # 20% tail-latency rise

# Minimum repeated samples a group needs before it counts as regression
# evidence. Groups below this are snapshots, per the variance note, and are
# skipped (never fail the gate) but reported as ``skipped``.
MIN_REGRESSION_SAMPLES = 2


@dataclass(frozen=True, slots=True)
class GroupComparison:
    """Per-group baseline-vs-candidate comparison for the regression gate."""

    server: str
    workload: str
    workers: int
    baseline_req_per_sec: float
    candidate_req_per_sec: float
    req_per_sec_change: float
    baseline_p99_latency_ms: float
    candidate_p99_latency_ms: float
    p99_latency_change: float
    regressed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """Outcome of comparing a candidate artifact against a baseline."""

    comparisons: list[GroupComparison] = field(default_factory=list)
    regressions: list[GroupComparison] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)

    @property
    def regressed(self) -> bool:
        """Return whether any comparable group regressed beyond tolerance."""
        return bool(self.regressions)


def _artifact_groups(artifact: dict) -> dict[tuple[str, str, int], dict]:
    """Index an artifact's variance groups by (server, workload, workers)."""
    variance = artifact.get("variance") or {}
    groups = variance.get("groups") or []
    indexed: dict[tuple[str, str, int], dict] = {}
    for group in groups:
        key = (
            str(group.get("server", "unknown")),
            str(group.get("workload", "unknown")),
            int(group.get("workers", 0)),
        )
        indexed[key] = group
    return indexed


def _group_median(group: dict, metric: str) -> float | None:
    """Return the median value for ``metric`` in a variance group, if present."""
    summary = group.get(metric)
    if not isinstance(summary, dict):
        return None
    median = summary.get("median")
    return float(median) if median is not None else None


def compare_artifact(
    baseline: dict,
    candidate: dict,
    *,
    rps_tolerance: float = DEFAULT_RPS_TOLERANCE,
    p99_tolerance: float = DEFAULT_P99_TOLERANCE,
    min_samples: int = MIN_REGRESSION_SAMPLES,
) -> ComparisonReport:
    """Compare a candidate artifact against a stored baseline artifact.

    For each ``(server, workload, workers)`` group present in both artifacts
    with enough repeated samples, flag a regression when median ``req_per_sec``
    drops by more than ``rps_tolerance`` (fractional, e.g. 0.10 == 10%) or
    median ``p99_latency_ms`` rises by more than ``p99_tolerance``.

    Groups with fewer than ``min_samples`` repeats in either artifact are
    skipped (snapshots, not regression evidence, per the variance note) and
    never fail the gate. Candidate groups absent from the baseline are reported
    as ``missing`` and likewise do not fail the gate.

    Returns a :class:`ComparisonReport`; ``report.regressed`` is the pass/fail
    signal the CLI gate turns into an exit code.
    """
    baseline_groups = _artifact_groups(baseline)
    candidate_groups = _artifact_groups(candidate)

    comparisons: list[GroupComparison] = []
    regressions: list[GroupComparison] = []
    skipped: list[dict] = []
    missing: list[dict] = []

    for key, cand_group in sorted(candidate_groups.items()):
        server, workload, workers = key
        base_group = baseline_groups.get(key)
        if base_group is None:
            missing.append({"server": server, "workload": workload, "workers": workers})
            continue

        base_samples = int(base_group.get("sample_count", 0))
        cand_samples = int(cand_group.get("sample_count", 0))
        if base_samples < min_samples or cand_samples < min_samples:
            skipped.append(
                {
                    "server": server,
                    "workload": workload,
                    "workers": workers,
                    "baseline_sample_count": base_samples,
                    "candidate_sample_count": cand_samples,
                    "reason": f"sample_count < {min_samples} (snapshot, not regression evidence)",
                }
            )
            continue

        base_rps = _group_median(base_group, "req_per_sec")
        cand_rps = _group_median(cand_group, "req_per_sec")
        base_p99 = _group_median(base_group, "p99_latency_ms")
        cand_p99 = _group_median(cand_group, "p99_latency_ms")
        if base_rps is None or cand_rps is None or base_p99 is None or cand_p99 is None:
            skipped.append(
                {
                    "server": server,
                    "workload": workload,
                    "workers": workers,
                    "reason": "missing median metrics",
                }
            )
            continue

        # Fractional change: negative req/s change == throughput drop;
        # positive p99 change == latency rise.
        rps_change = (cand_rps - base_rps) / base_rps if base_rps else 0.0
        p99_change = (cand_p99 - base_p99) / base_p99 if base_p99 else 0.0

        reasons: list[str] = []
        if rps_change < -rps_tolerance:
            reasons.append(
                f"req/s regressed {rps_change * -100:.1f}% "
                f"(median {base_rps:,.0f} -> {cand_rps:,.0f}, "
                f"tolerance {rps_tolerance * 100:.0f}%)"
            )
        if p99_change > p99_tolerance:
            reasons.append(
                f"p99 latency rose {p99_change * 100:.1f}% "
                f"(median {base_p99:.2f}ms -> {cand_p99:.2f}ms, "
                f"tolerance {p99_tolerance * 100:.0f}%)"
            )

        comparison = GroupComparison(
            server=server,
            workload=workload,
            workers=workers,
            baseline_req_per_sec=base_rps,
            candidate_req_per_sec=cand_rps,
            req_per_sec_change=rps_change,
            baseline_p99_latency_ms=base_p99,
            candidate_p99_latency_ms=cand_p99,
            p99_latency_change=p99_change,
            regressed=bool(reasons),
            reasons=reasons,
        )
        comparisons.append(comparison)
        if comparison.regressed:
            regressions.append(comparison)

    return ComparisonReport(
        comparisons=comparisons,
        regressions=regressions,
        skipped=skipped,
        missing=missing,
    )


def print_comparison_report(report: ComparisonReport) -> None:
    """Print a human-readable regression-gate report."""
    print("\n## Regression Gate (candidate vs baseline)\n")
    if not report.comparisons and not report.skipped and not report.missing:
        print("No comparable groups found between baseline and candidate artifacts.")
        return

    print("| Server | Workload | Workers | d req/s | d p99 | Status |")
    print("|--------|----------|---------|---------|-------|--------|")
    for c in report.comparisons:
        status = "REGRESSED" if c.regressed else "ok"
        print(
            f"| {c.server} | {c.workload} | {c.workers} | "
            f"{c.req_per_sec_change * 100:+.1f}% | {c.p99_latency_change * 100:+.1f}% | {status} |"
        )

    for c in report.regressions:
        for reason in c.reasons:
            print(f"  - REGRESSION [{c.server}/{c.workload}/{c.workers}w]: {reason}")

    for entry in report.skipped:
        print(
            f"  - skipped [{entry['server']}/{entry['workload']}/{entry['workers']}w]: "
            f"{entry['reason']}"
        )
    for entry in report.missing:
        print(
            f"  - missing from baseline "
            f"[{entry['server']}/{entry['workload']}/{entry['workers']}w] "
            "(not gated)"
        )

    if report.regressed:
        print(f"\nFAIL: {len(report.regressions)} group(s) regressed beyond tolerance.")
    else:
        print("\nPASS: no group regressed beyond tolerance.")


def load_artifact(path: Path) -> dict:
    """Load a benchmark artifact JSON file."""
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        msg = f"artifact {path} is not a JSON object"
        raise ValueError(msg)
    return data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pounce benchmark runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workload",
        default="hello",
        choices=[*WORKLOADS, "all"],
        help="Workload to benchmark (default: hello)",
    )
    parser.add_argument("--workers", type=int, default=1, help="Worker count (default: 1)")
    parser.add_argument(
        "--duration", type=int, default=10, help="Test duration in seconds (default: 10)"
    )
    parser.add_argument("--threads", type=int, default=4, help="wrk/hey thread count (default: 4)")
    parser.add_argument(
        "--connections", type=int, default=100, help="Concurrent connections (default: 100)"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each workload this many times (default: 1)",
    )
    parser.add_argument(
        "--compare", action="store_true", help="Also benchmark uvicorn for comparison"
    )
    parser.add_argument(
        "--servers",
        default=None,
        help=("Comma-separated servers: pounce,uvicorn,hypercorn,granian. Overrides --compare."),
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=None,
        help=(
            "Schedule a fixed request rate using the built-in coordinated-omission-safe "
            "driver; enables p999 reporting without an external load tool"
        ),
    )
    parser.add_argument("--output", type=str, default=None, help="Save results to JSON file")
    parser.add_argument(
        "--artifact-output",
        type=str,
        default=None,
        help="Save artifact-schema-compatible metadata JSON",
    )
    parser.add_argument(
        "--compare-baseline",
        type=str,
        default=None,
        help=(
            "Regression gate: compare this run against a committed baseline "
            "artifact JSON and exit non-zero when a metric regresses beyond "
            "tolerance. Groups with sample_count < 2 are skipped."
        ),
    )
    parser.add_argument(
        "--rps-tolerance",
        type=float,
        default=DEFAULT_RPS_TOLERANCE,
        help=(
            "Allowed fractional median req/s drop before the gate fails "
            f"(default: {DEFAULT_RPS_TOLERANCE}, i.e. {DEFAULT_RPS_TOLERANCE * 100:.0f}%%)"
        ),
    )
    parser.add_argument(
        "--p99-tolerance",
        type=float,
        default=DEFAULT_P99_TOLERANCE,
        help=(
            "Allowed fractional median p99 latency rise before the gate fails "
            f"(default: {DEFAULT_P99_TOLERANCE}, i.e. {DEFAULT_P99_TOLERANCE * 100:.0f}%%)"
        ),
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    if args.duration < 1:
        parser.error("--duration must be >= 1")
    if args.connections < 1:
        parser.error("--connections must be >= 1")
    if args.rate is not None and args.rate < 1:
        parser.error("--rate must be >= 1")
    if args.rps_tolerance < 0 or args.p99_tolerance < 0:
        parser.error("--rps-tolerance and --p99-tolerance must be >= 0")

    valid_servers = {"pounce", "uvicorn", "hypercorn", "granian"}
    if args.servers:
        selected_servers = tuple(
            dict.fromkeys(server.strip().lower() for server in args.servers.split(","))
        )
    else:
        selected_servers = ("pounce", "uvicorn") if args.compare else ("pounce",)
    unknown_servers = sorted(set(selected_servers) - valid_servers)
    if unknown_servers:
        parser.error(f"unknown --servers value(s): {', '.join(unknown_servers)}")
    if "pounce" not in selected_servers:
        parser.error("--servers must include pounce")

    suite = BenchmarkSuite(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        python_version=sys.version,
        platform=platform.platform(),
    )

    workloads = list(WORKLOADS) if args.workload == "all" else [args.workload]
    all_results: list[BenchmarkResult] = []

    print("Pounce Benchmark Suite")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    load_tool = "pounce-fixed-rate" if args.rate is not None else _find_load_tool()
    print(f"Tool: {load_tool}")

    for sample_index, wl in _sample_plan(workloads, args.repeat):
        print(f"\n{'=' * 60}")
        print(f"Workload: {wl} — {WORKLOADS[wl]['description']}")
        if args.repeat > 1:
            print(f"Sample: {sample_index}/{args.repeat}")
        print(f"{'=' * 60}")

        results = run_benchmark(
            workload=wl,
            workers=args.workers,
            duration=args.duration,
            threads=args.threads,
            connections=args.connections,
            compare=args.compare,
            servers=selected_servers,
            load_tool=load_tool,
            rate=args.rate,
            sample_index=sample_index,
        )
        all_results.extend(results)

    suite.results = [asdict(r) for r in all_results]

    print_markdown_table(all_results)

    if args.output:
        save_json(suite, Path(args.output))

    # Build the candidate artifact once if either output or the regression
    # gate needs it.
    artifact: dict | None = None
    if args.artifact_output or args.compare_baseline:
        artifact = build_artifact(
            suite,
            command=[sys.executable, *sys.argv],
            workload=args.workload,
            workers=args.workers,
            duration=args.duration,
            connections=args.connections,
            threads=args.threads,
            load_tool=load_tool,
            load_tool_version=_load_tool_version(load_tool),
            compare=args.compare,
            servers=selected_servers,
            target_rps=args.rate,
        )

    if args.artifact_output and artifact is not None:
        save_artifact(artifact, Path(args.artifact_output))

    if args.compare_baseline and artifact is not None:
        baseline = load_artifact(Path(args.compare_baseline))
        report = compare_artifact(
            baseline,
            artifact,
            rps_tolerance=args.rps_tolerance,
            p99_tolerance=args.p99_tolerance,
        )
        print_comparison_report(report)
        if report.regressed:
            sys.exit(1)


if __name__ == "__main__":
    main()
