"""Standardized performance benchmarks for pounce.

Spawns a pounce server with built-in ASGI apps and drives load using
``http.client`` from multiple threads.  Reports throughput, latency
percentiles, and memory usage.

"""

from __future__ import annotations

import contextlib
import http.client
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import (
    dataclass,
    field,
)
from typing import Any

# ── Inline ASGI benchmark apps ──────────────────────────────────────

# These are written as module-level script strings so they can be passed
# to the server subprocess without importing pounce internals.

_BENCH_APP_SOURCE = textwrap.dedent("""\
    from __future__ import annotations

    import json as _json

    async def app(scope, receive, send):
        path = scope.get("path", "/")

        if path == "/hello":
            body = b"Hello, World!"
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                    [b"content-length", str(len(body)).encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})

        elif path == "/json":
            payload = _json.dumps({"message": "Hello, World!", "status": "ok"}).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(payload)).encode()],
                ],
            })
            await send({"type": "http.response.body", "body": payload})

        elif path == "/body":
            # Read the request body and echo it back
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body", False):
                    break
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/octet-stream"],
                    [b"content-length", str(len(body)).encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})

        else:
            body = b"Not Found"
            await send({
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    [b"content-type", b"text/plain"],
                    [b"content-length", str(len(body)).encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})
""")


# ── Data classes ─────────────────────────────────────────────────────


@dataclass(slots=True)
class WorkloadResult:
    """Results from a single workload run."""

    name: str
    total_requests: int
    errors: int
    duration_s: float
    latencies_ms: list[float]
    rss_mb: float

    @property
    def rps(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return self.total_requests / self.duration_s

    @property
    def p50(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.50)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p95(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]


@dataclass(slots=True)
class BenchSuite:
    """Results from a complete benchmark suite."""

    label: str
    workers: int
    connections: int
    duration: int
    workloads: list[WorkloadResult] = field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────


def _find_free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_rss_mb(pid: int) -> float:
    """Get resident set size in MB for a process.

    Tries platform-specific approaches in order:
    1. /proc/{pid}/status (Linux)
    2. ps command (macOS / BSD)
    """
    # Try /proc (Linux)
    proc_status = f"/proc/{pid}/status"
    try:
        with open(proc_status) as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # Value is in kB
                    kb = int(line.split()[1])
                    return kb / 1024.0
    except OSError:
        # FileNotFoundError and PermissionError are OSError subclasses.
        pass
    except ValueError:
        pass

    # Try ps (macOS / BSD / Linux fallback)
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            kb = int(result.stdout.strip())
            return kb / 1024.0
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        pass
    except ValueError:
        pass

    return 0.0


def _wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
    """Wait until a server is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/hello")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return True
        except OSError:
            # ConnectionRefusedError is an OSError subclass.
            time.sleep(0.1)
        except http.client.HTTPException:
            time.sleep(0.1)
    return False


def _write_bench_app(path: str) -> None:
    """Write the benchmark ASGI app to a temporary file."""
    with open(path, "w") as f:
        f.write(_BENCH_APP_SOURCE)


# ── Output helpers ───────────────────────────────────────────────────

# `pounce bench` is a convenience driver: an http.client thread driver that
# prints a plain-text table. It is NOT the governed artifact pipeline. Public
# numeric performance claims must come from `benchmarks/run_benchmark.py`, which
# emits schema-compatible artifacts (`benchmarks/artifact-schema.json`) with
# git SHA, variance, raw load-tool output, and process telemetry. This banner
# keeps the CLI snapshot from being mistaken for a governed artifact.
_SNAPSHOT_CAVEAT = (
    "  LOCAL SNAPSHOT - not a governed benchmark artifact.\n"
    "  These numbers are an ad-hoc local measurement, not a product claim.\n"
    "  For reproducible, citable evidence (git SHA, variance, CPU/RSS telemetry)\n"
    "  use: python benchmarks/run_benchmark.py --artifact-output <path>\n"
    "  See benchmarks/artifact-schema.json and benchmarks/README.md."
)


def _emit(msg: str) -> None:
    """Write a benchmark progress message to stderr."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ── Load driver ──────────────────────────────────────────────────────


def _drive_load(
    host: str,
    port: int,
    path: str,
    method: str,
    body: bytes | None,
    duration: float,
    connections: int,
) -> tuple[int, int, list[float]]:
    """Drive HTTP load from multiple threads.

    Returns (total_requests, errors, latencies_ms).
    """
    total_requests = 0
    total_errors = 0
    all_latencies: list[float] = []
    lock = threading.Lock()
    stop_event = threading.Event()

    def worker() -> None:
        nonlocal total_requests, total_errors
        local_count = 0
        local_errors = 0
        local_latencies: list[float] = []

        try:
            conn = http.client.HTTPConnection(host, port, timeout=5)
        except Exception:
            return

        headers = {}
        if body is not None:
            headers["Content-Length"] = str(len(body))
            headers["Content-Type"] = "application/octet-stream"

        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                conn.request(method, path, body=body, headers=headers)
                resp = conn.getresponse()
                resp.read()
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                local_latencies.append(elapsed_ms)
                local_count += 1
            except Exception:
                local_errors += 1
                # Reconnect on error
                with contextlib.suppress(Exception):
                    conn.close()
                with contextlib.suppress(Exception):
                    conn = http.client.HTTPConnection(host, port, timeout=5)

        with contextlib.suppress(Exception):
            conn.close()

        with lock:
            total_requests += local_count
            total_errors += local_errors
            all_latencies.extend(local_latencies)

    threads = []
    for _ in range(connections):
        t = threading.Thread(target=worker, daemon=True)
        threads.append(t)
        t.start()

    time.sleep(duration)
    stop_event.set()

    for t in threads:
        t.join(timeout=5)

    return total_requests, total_errors, all_latencies


def _run_bench(
    server_cmd: list[str],
    label: str,
    duration: int,
    connections: int,
    host: str,
    port: int,
) -> BenchSuite:
    """Run a full benchmark suite against a server command.

    Starts the server, runs each workload, collects results, and stops
    the server.
    """
    import tempfile

    # Write bench app to temp file
    tmpdir = tempfile.mkdtemp(prefix="pounce_bench_")
    app_path = os.path.join(tmpdir, "_bench_app.py")
    _write_bench_app(app_path)

    # Add tmpdir to PYTHONPATH so the server subprocess can import _bench_app
    env = {**os.environ, "PYTHONPATH": tmpdir + os.pathsep + os.environ.get("PYTHONPATH", "")}

    # Build the full command with the app reference using the target server's CLI style.
    is_uvicorn = (bool(server_cmd) and os.path.basename(server_cmd[0]) == "uvicorn") or (
        len(server_cmd) >= 3 and server_cmd[1] == "-m" and server_cmd[2] == "uvicorn"
    )
    if is_uvicorn:
        full_cmd = [*server_cmd, "_bench_app:app"]
    else:
        full_cmd = [*server_cmd, "--app", "_bench_app:app"]

    suite = BenchSuite(label=label, workers=0, connections=connections, duration=duration)

    # Start server — capture stderr so we can show it on failure
    proc = subprocess.Popen(
        full_cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    try:
        if not _wait_for_server(host, port, timeout=15.0):
            _emit(f"  [{label}] Server failed to start, skipping.")
            stderr_output = ""
            if proc.stderr:
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace").strip()
            if stderr_output:
                for line in stderr_output.splitlines()[-10:]:
                    _emit(f"  [{label}]   {line}")
            proc.terminate()
            proc.wait(timeout=5)
            return suite

        server_pid = proc.pid

        # Workload 1: hello (minimal plain text)
        _emit(f"  [{label}] Running 'hello' workload ({duration}s)...")
        reqs, errs, lats = _drive_load(host, port, "/hello", "GET", None, duration, connections)
        rss = _get_rss_mb(server_pid)
        suite.workloads.append(
            WorkloadResult(
                name="hello",
                total_requests=reqs,
                errors=errs,
                duration_s=duration,
                latencies_ms=lats,
                rss_mb=rss,
            )
        )

        # Workload 2: json (JSON response)
        _emit(f"  [{label}] Running 'json' workload ({duration}s)...")
        reqs, errs, lats = _drive_load(host, port, "/json", "GET", None, duration, connections)
        rss = _get_rss_mb(server_pid)
        suite.workloads.append(
            WorkloadResult(
                name="json",
                total_requests=reqs,
                errors=errs,
                duration_s=duration,
                latencies_ms=lats,
                rss_mb=rss,
            )
        )

        # Workload 3: body (POST echo 1KB)
        post_body = b"x" * 1024
        _emit(f"  [{label}] Running 'body' workload ({duration}s)...")
        reqs, errs, lats = _drive_load(
            host, port, "/body", "POST", post_body, duration, connections
        )
        rss = _get_rss_mb(server_pid)
        suite.workloads.append(
            WorkloadResult(
                name="body",
                total_requests=reqs,
                errors=errs,
                duration_s=duration,
                latencies_ms=lats,
                rss_mb=rss,
            )
        )

    finally:
        # Stop the server
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

        # Clean up temp files (shutil handles __pycache__ etc.)
        import shutil

        with contextlib.suppress(OSError):
            shutil.rmtree(tmpdir, ignore_errors=True)

    return suite


# ── Results formatting ───────────────────────────────────────────────


def _format_results(suites: list[BenchSuite]) -> str:
    """Render benchmark results as a plain text table."""
    lines: list[str] = []

    # Header
    lines.append("")
    lines.append("=" * 90)
    lines.append("  Pounce Benchmark Results (local snapshot)")
    lines.append("=" * 90)
    lines.append(_SNAPSHOT_CAVEAT)
    lines.append("=" * 90)
    lines.append("")

    for suite in suites:
        lines.append(f"  Server: {suite.label}")
        lines.append(f"  Connections: {suite.connections}  |  Duration: {suite.duration}s")
        lines.append("-" * 90)
        lines.append(
            f"  {'Workload':<10s}  {'Req/s':>10s}  {'p50 (ms)':>10s}  "
            f"{'p95 (ms)':>10s}  {'p99 (ms)':>10s}  {'Errors':>8s}  {'RSS (MB)':>10s}"
        )
        lines.append("-" * 90)

        lines.extend(
            f"  {w.name:<10s}  {w.rps:>10.0f}  {w.p50:>10.2f}  "
            f"{w.p95:>10.2f}  {w.p99:>10.2f}  {w.errors:>8d}  {w.rss_mb:>10.1f}"
            for w in suite.workloads
        )

        lines.append("")

    # Comparison table if multiple suites
    if len(suites) > 1:
        lines.append("=" * 90)
        lines.append("  Comparison (pounce vs others)")
        lines.append("=" * 90)
        base = suites[0]
        for other in suites[1:]:
            lines.append(f"  {base.label} vs {other.label}:")
            lines.append(
                f"  {'Workload':<10s}  "
                f"{base.label + ' req/s':>15s}  "
                f"{other.label + ' req/s':>15s}  "
                f"{'Ratio':>10s}"
            )
            lines.append("-" * 90)
            for bw, ow in zip(base.workloads, other.workloads, strict=True):
                ratio = bw.rps / ow.rps if ow.rps > 0 else float("inf")
                lines.append(f"  {bw.name:<10s}  {bw.rps:>15.0f}  {ow.rps:>15.0f}  {ratio:>10.2f}x")
            lines.append("")

    lines.append("=" * 90)
    lines.append(_SNAPSHOT_CAVEAT)
    lines.append("=" * 90)
    lines.append("")
    return "\n".join(lines)


# ── CLI registration ─────────────────────────────────────────────────


def register_bench_command(cli: Any) -> None:
    """Register the ``bench`` subcommand on the CLI."""

    @cli.command(
        "bench",
        description="Run a local benchmark snapshot (not a governed artifact)",
        display_result=False,
    )
    def bench(
        workers: int = 1,
        duration: int = 10,
        connections: int = 50,
        compare: bool = False,
    ) -> None:
        """Run a local benchmark snapshot.

        Spawns pounce (and optionally uvicorn) with a built-in ASGI app
        and drives load from multiple threads.  Reports throughput,
        latency percentiles, and RSS memory.

        This is a convenience driver, NOT the governed artifact pipeline.
        Its output is labelled a local snapshot and must not be cited as a
        product claim. For reproducible, citable evidence (git SHA, variance,
        CPU/RSS telemetry) use ``benchmarks/run_benchmark.py --artifact-output``,
        which emits artifacts following ``benchmarks/artifact-schema.json``.

        Args:
            workers: Number of server workers.
            duration: Duration of each workload in seconds.
            connections: Number of concurrent connections.
            compare: Also benchmark uvicorn for comparison.
        """
        suites: list[BenchSuite] = []

        port = _find_free_port()
        host = "127.0.0.1"

        _emit("")
        _emit("Pounce Benchmark")
        _emit(f"  Workers: {workers}  |  Duration: {duration}s  |  Connections: {connections}")
        _emit("")

        # Bench pounce
        pounce_cmd = [
            sys.executable,
            "-m",
            "pounce",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--no-access-log",
            "--log-level",
            "warning",
        ]
        _emit("[pounce] Starting benchmark...")
        pounce_suite = _run_bench(pounce_cmd, "pounce", duration, connections, host, port)
        pounce_suite.workers = workers
        suites.append(pounce_suite)

        # Optionally bench uvicorn
        if compare:
            try:
                import uvicorn  # noqa: F401

                uvi_port = _find_free_port()
                uvi_cmd = [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "--host",
                    host,
                    "--port",
                    str(uvi_port),
                    "--workers",
                    str(workers),
                    "--log-level",
                    "warning",
                ]
                _emit("[uvicorn] Starting benchmark...")
                uvi_suite = _run_bench(uvi_cmd, "uvicorn", duration, connections, host, uvi_port)
                uvi_suite.workers = workers
                suites.append(uvi_suite)
            except ImportError:
                _emit("[uvicorn] Not installed, skipping comparison.")
                _emit("  Install with: pip install uvicorn")
                _emit("")

        # Print results
        output = _format_results(suites)
        _emit(output)
