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

Prerequisites:
    brew install wrk    # or: go install github.com/rakyll/hey@latest

"""

import argparse
import json
import platform
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    transfer_per_sec: str
    total_requests: int
    errors: int
    sample_index: int = 1


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
        "description": "Bengal-shaped generated static site",
        "method": "GET",
        "path": "/",
    },
    "chirp": {
        "app": "benchmarks.apps.chirp_forum:app",
        "description": "Chirp/LB Sonic-shaped multi-tenant forum thread",
        "method": "GET",
        "path": "/threads/1",
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
    msg = f"unknown benchmark server: {server}"
    raise ValueError(msg)


def _command_string(command: list[str]) -> str:
    """Render a command list for artifact metadata."""
    return " ".join(command)


def _sample_plan(workloads: list[str], repeat: int) -> list[tuple[int, str]]:
    """Return the ordered benchmark samples to run."""
    if repeat < 1:
        msg = "repeat must be >= 1"
        raise ValueError(msg)
    return [
        (sample_index, workload)
        for sample_index in range(1, repeat + 1)
        for workload in workloads
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
        except ConnectionRefusedError, OSError:
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
        except FileNotFoundError, subprocess.TimeoutExpired:
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
    for flag in ("--version", "-version", "--help"):
        try:
            result = subprocess.run(
                [tool, flag],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
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
    return _parse_wrk_output(result.stdout)


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
    return _parse_hey_output(result.stdout)


def _parse_wrk_output(output: str) -> dict:
    """Parse wrk --latency output into a result dict."""
    result: dict = {
        "req_per_sec": 0.0,
        "avg_latency_ms": 0.0,
        "p50_latency_ms": 0.0,
        "p99_latency_ms": 0.0,
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
    port: int = 8100,
    sample_index: int = 1,
) -> list[BenchmarkResult]:
    """Run a benchmark for a single workload, optionally comparing servers."""
    wl = WORKLOADS[workload]
    tool = _find_load_tool()
    runner = _run_wrk if tool == "wrk" else _run_hey
    results: list[BenchmarkResult] = []

    method = wl.get("method", "GET")
    body_size = wl.get("body_size")

    # --- Pounce ---
    print(f"\n  Starting pounce ({workload}, {workers} workers)...")
    pounce_cmd = _server_command("pounce", workload, port, workers)
    pounce_proc = _start_server(pounce_cmd, port)
    time.sleep(0.5)

    try:
        print(f"  Running {tool} ({duration}s, {connections} connections)...")
        raw = runner(
            _benchmark_url(port, workload),
            duration=duration,
            threads=threads,
            connections=connections,
            method=method,
            body_size=body_size,
        )
        results.append(
            BenchmarkResult(
                server="pounce",
                workload=workload,
                workers=workers,
                duration_s=duration,
                threads=threads,
                connections=connections,
                sample_index=sample_index,
                **raw,
            )
        )
    finally:
        _stop_server(pounce_proc)

    # --- Uvicorn (optional comparison) ---
    if compare:
        uvicorn_port = port + 1
        print(f"\n  Starting uvicorn ({workload}, {workers} workers)...")
        uvicorn_cmd = _server_command("uvicorn", workload, uvicorn_port, workers)
        try:
            uvicorn_proc = _start_server(uvicorn_cmd, uvicorn_port)
            time.sleep(0.5)

            print(f"  Running {tool} ({duration}s, {connections} connections)...")
            raw = runner(
                _benchmark_url(uvicorn_port, workload),
                duration=duration,
                threads=threads,
                connections=connections,
                method=method,
                body_size=body_size,
            )
            results.append(
                BenchmarkResult(
                    server="uvicorn",
                    workload=workload,
                    workers=workers,
                    duration_s=duration,
                    threads=threads,
                    connections=connections,
                    sample_index=sample_index,
                    **raw,
                )
            )
            _stop_server(uvicorn_proc)
        except (RuntimeError, FileNotFoundError) as exc:
            print(f"  Uvicorn comparison skipped: {exc}", file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_markdown_table(results: list[BenchmarkResult]) -> None:
    """Print results as a markdown table."""
    print("\n## Benchmark Results\n")
    print("| Server | Workload | Workers | Req/s | Avg (ms) | p50 (ms) | p99 (ms) | Errors |")
    print("|--------|----------|---------|-------|----------|----------|----------|--------|")
    for r in results:
        print(
            f"| {r.server} | {r.workload} | {r.workers} | "
            f"{r.req_per_sec:,.0f} | {r.avg_latency_ms:.2f} | "
            f"{r.p50_latency_ms:.2f} | {r.p99_latency_ms:.2f} | {r.errors} |"
        )

    # Print pounce vs uvicorn ratios if comparison data exists
    pounce_results = [r for r in results if r.server == "pounce"]
    uvicorn_results = [r for r in results if r.server == "uvicorn"]

    if pounce_results and uvicorn_results:
        print("\n### Throughput Ratio (pounce / uvicorn)\n")
        for p in pounce_results:
            for u in uvicorn_results:
                if p.workload == u.workload and p.workers == u.workers:
                    ratio = p.req_per_sec / u.req_per_sec if u.req_per_sec > 0 else 0
                    print(f"- {p.workload} ({p.workers}w): **{ratio:.2f}x**")


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
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip()


def _python_gil_mode() -> str:
    """Return whether this Python build currently has the GIL enabled."""
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    if callable(is_gil_enabled):
        return "gil-enabled" if is_gil_enabled() else "free-threaded"
    return "unknown"


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
    port: int = 8100,
) -> dict:
    """Build JSON metadata matching benchmarks/artifact-schema.json."""
    created_at = suite.timestamp or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    server_commands: dict[str, str] = {}
    workloads = list(WORKLOADS) if workload == "all" else [workload]
    for wl in workloads:
        server_commands[f"pounce:{wl}"] = _command_string(
            _server_command("pounce", wl, port, workers)
        )
        if compare:
            server_commands[f"uvicorn:{wl}"] = _command_string(
                _server_command("uvicorn", wl, port + 1, workers)
            )

    return {
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
        "load_tool": load_tool,
        "load_tool_version": load_tool_version,
        "comparison_target": "uvicorn" if compare else None,
        "comparison_target_version": "recorded in raw output when available" if compare else None,
        "samples": suite.results,
        "variance": {
            "sample_count": 1,
            "req_per_sec": 0.0,
            "p99_latency_ms": 0.0,
            "note": "single runner sample; repeat artifacts before making regression claims",
        },
        "raw_output": suite.results,
        "summary": {
            "results": suite.results,
        },
    }


def save_artifact(artifact: dict, path: Path) -> None:
    """Save a benchmark artifact JSON file."""
    path.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"\nBenchmark artifact saved to {path}")


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
    parser.add_argument("--output", type=str, default=None, help="Save results to JSON file")
    parser.add_argument(
        "--artifact-output",
        type=str,
        default=None,
        help="Save artifact-schema-compatible metadata JSON",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

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
    load_tool = _find_load_tool()
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
            sample_index=sample_index,
        )
        all_results.extend(results)

    suite.results = [asdict(r) for r in all_results]

    print_markdown_table(all_results)

    if args.output:
        save_json(suite, Path(args.output))

    if args.artifact_output:
        artifact = build_artifact(
            suite,
            command=sys.argv,
            workload=args.workload,
            workers=args.workers,
            duration=args.duration,
            connections=args.connections,
            threads=args.threads,
            load_tool=load_tool,
            load_tool_version=_load_tool_version(load_tool),
            compare=args.compare,
        )
        save_artifact(artifact, Path(args.artifact_output))


if __name__ == "__main__":
    main()
