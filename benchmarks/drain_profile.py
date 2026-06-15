#!/usr/bin/env python3
"""Reload/drain-under-load benchmark profile for pounce (#141).

Drives a steady mixed load — keep-alive ``/fast`` + in-flight ``/slow`` +
``/stream`` — through the *real* ``pounce serve`` CLI subprocess (the
``benchmarks/apps/drain_probe.py`` surface), fires ``SIGHUP`` (reload) and then
``SIGTERM`` (drain+exit), and records the four drain-contract properties as an
artifact-schema-compatible JSON (``benchmarks/artifact-schema.json``):

1. in-flight ``/slow`` + ``/stream`` requests complete fully across the drain,
2. brand-new connections arriving during drain get a *bounded* clean refusal
   (503 / clean close) rather than hangs or garbage (the disconnect rate),
3. the process exits within ``shutdown_timeout`` (the drain duration), and
4. no orphan worker processes survive after exit.

This is the missing "reload/drain-under-load artifact via the real CLI"
evidence; it reuses the harness pattern proven by
``tests/integration/test_signal_lifecycle.py::test_sigterm_drains_under_mixed_load``.
The cross-worker-mode artifact (async / sync / subinterpreter / process) is
generated on a free-threaded 3.14t CI lane — the sync execution path only
activates in thread mode there. Locally (GIL build) the ``async`` mode runs as
forked process workers, exercising the process-drain path.

Usage:
    python benchmarks/drain_profile.py --worker-mode async --duration 4
    python benchmarks/drain_profile.py --worker-mode subinterpreter \
        --artifact-output benchmarks/artifacts/<date>/drain.json

Requires: pounce installed in editable mode (``uv sync --group dev``).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Allow ``python benchmarks/drain_profile.py`` as well as
# ``python -m benchmarks.drain_profile`` by ensuring the repo root (the parent
# of this ``benchmarks/`` directory) is importable.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from benchmarks.run_benchmark import (  # noqa: E402 - path bootstrap must precede import
    _command_string,
    build_profile_artifact,
    save_artifact,
)

# The drain probe exposes the fast/slow/stream/version surface used by every
# drain code path (see benchmarks/apps/drain_probe.py).
DRAIN_APP = "benchmarks.apps.drain_probe:app"
DRAIN_WORKLOAD = "reload_drain"

# Sentinels recorded per brand-new-connection attempt so a CLEAN refusal
# (connection refused/reset with no bytes) is told apart from a HUNG attempt
# (accepted then never answered -> read timeout, a silent drop). Mirrors the
# integration harness so the artifact's "disconnect rate" is non-tautological.
_REFUSED = b"\x00REFUSED"
_HUNG = b"\x00HUNG"
_CONNECT_ERRORS = (ConnectionError, OSError, TimeoutError)


def _server_command(
    port: int, *, workers: int, worker_mode: str, shutdown_timeout: int
) -> list[str]:
    """Build the real-CLI ``pounce serve`` command for the drain probe app."""
    return [
        sys.executable,
        "-m",
        "pounce",
        "serve",
        "--app",
        DRAIN_APP,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--worker-mode",
        worker_mode,
        "--shutdown-timeout",
        str(shutdown_timeout),
        "--no-access-log",
        "--signage",
        "off",
    ]


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _server_env() -> dict[str, str]:
    """Subprocess env: import pounce + benchmarks from this checkout, no .pyc."""
    env = os.environ.copy()
    pythonpath = f"{_REPO_ROOT}{os.pathsep}{Path(_REPO_ROOT) / 'src'}"
    if env.get("PYTHONPATH"):
        pythonpath = f"{pythonpath}{os.pathsep}{env['PYTHONPATH']}"
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _probe_request(port: int, path: str, *, timeout: float = 8.0) -> bytes:
    """Send one ``Connection: close`` request and return the full response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", port))
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
        )
        response = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        return bytes(response)
    finally:
        sock.close()


def _wait_for_probe(port: int, *, timeout: float = 12.0) -> bool:
    """Wait until the drain probe answers ``/fast`` (server is ready)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if b"fast-ok" in _probe_request(port, "/fast", timeout=1.0):
                return True
        except _CONNECT_ERRORS:
            time.sleep(0.1)
    return False


def _child_pids(pid: int) -> list[int]:
    """Return direct child PIDs of *pid* via pgrep (empty if none/unsupported)."""
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # fmt: skip
        return []
    return [int(line) for line in out.stdout.split() if line.strip().isdigit()]


def _classify_new_connection(result: bytes) -> str:
    """Bucket a brand-new-connection outcome during drain.

    Returns one of ``served_200``, ``refused_503``, ``clean_close``, ``hung``,
    or ``garbage``. ``hung``/``garbage`` are silent-drop failures.
    """
    if result == _HUNG:
        return "hung"
    if result in (b"", _REFUSED):
        return "clean_close"
    if b" 503 " in result or b"503 Service Unavailable" in result:
        return "refused_503"
    if b" 200 " in result:
        return "served_200"
    return "garbage"


def summarize_drain(
    *,
    inflight_results: dict[str, bytes],
    inflight_expected: int,
    new_conn_results: list[bytes],
    drain_duration_s: float,
    shutdown_timeout: int,
    returncode: int | None,
    orphan_pids: list[int],
) -> dict[str, Any]:
    """Aggregate raw drain observations into a drain-metrics block.

    Pure function (no live server) so the artifact wiring is unit-testable. The
    four contract properties become numbers: ``inflight_completed`` (property 1),
    ``disconnect_rate`` over brand-new connections (property 2), ``drain_duration_s``
    bounded by ``shutdown_timeout`` (property 3), and ``orphan_workers`` (property 4).
    """
    inflight_completed = sum(
        1
        for body in inflight_results.values()
        if b" 200 " in body and (b"slow-done" in body or b"chunk-2" in body)
    )

    buckets = {
        "served_200": 0,
        "refused_503": 0,
        "clean_close": 0,
        "hung": 0,
        "garbage": 0,
    }
    for result in new_conn_results:
        buckets[_classify_new_connection(result)] += 1

    total_new = len(new_conn_results)
    # Disconnect rate: fraction of new connections cleanly refused (503 or
    # no-byte close). A draining server SHOULD refuse new work; the fault
    # signal is in ``silent_drops`` (hung/garbage), which must be zero.
    refusals = buckets["refused_503"] + buckets["clean_close"]
    silent_drops = buckets["hung"] + buckets["garbage"]
    disconnect_rate = round(refusals / total_new, 4) if total_new else 0.0
    drop_rate = round(silent_drops / total_new, 4) if total_new else 0.0

    exited_within_timeout = returncode == 0 and drain_duration_s <= shutdown_timeout + 12

    return {
        "inflight_expected": inflight_expected,
        "inflight_completed": inflight_completed,
        "inflight_completion_rate": (
            round(inflight_completed / inflight_expected, 4) if inflight_expected else 0.0
        ),
        "new_connections": total_new,
        "refusals": refusals,
        "disconnect_rate": disconnect_rate,
        "silent_drops": silent_drops,
        "drop_rate": drop_rate,
        "new_connection_buckets": buckets,
        "drain_duration_s": round(drain_duration_s, 3),
        "shutdown_timeout_s": shutdown_timeout,
        "exited_within_timeout": exited_within_timeout,
        "returncode": returncode,
        "orphan_workers": len(orphan_pids),
        "clean_drain": bool(
            inflight_completed == inflight_expected
            and silent_drops == 0
            and exited_within_timeout
            and not orphan_pids
        ),
    }


def drain_sample(
    drain: dict[str, Any],
    *,
    worker_mode: str,
    workers: int,
    sample_index: int,
) -> dict[str, Any]:
    """Map a drain-metrics block onto an artifact-schema sample row.

    The worker mode is recorded as the ``server`` so each mode forms its own
    ``(server, workload, workers)`` variance group, matching the worker-mode
    comparison profile. The schema's numeric surface is reused: ``req_per_sec``
    carries the in-flight completion rate (the headline drain-health number),
    ``p99_latency_ms`` the drain duration in ms, and ``errors`` the silent-drop
    count (so the regression gate flags a drain that starts dropping requests).
    """
    return {
        "server": worker_mode,
        "workload": DRAIN_WORKLOAD,
        "workers": workers,
        "duration_s": int(drain["drain_duration_s"]),
        "threads": 0,
        "connections": drain["new_connections"],
        "req_per_sec": float(drain["inflight_completion_rate"]),
        "avg_latency_ms": float(drain["drain_duration_s"] * 1000.0),
        "p50_latency_ms": float(drain["drain_duration_s"] * 1000.0),
        "p99_latency_ms": float(drain["drain_duration_s"] * 1000.0),
        "transfer_per_sec": "",
        "total_requests": int(drain["inflight_completed"]),
        "errors": int(drain["silent_drops"]),
        "sample_index": sample_index,
        "drain": drain,
    }


def run_drain_profile(
    *,
    worker_mode: str,
    workers: int,
    slow_requests: int,
    burst: int,
    shutdown_timeout: int,
    port: int | None = None,
) -> dict[str, Any]:
    """Drive mixed load through the real CLI, fire SIGHUP+SIGTERM, return a sample.

    Mirrors ``test_sigterm_drains_under_mixed_load``: keep-alive ``/fast`` loop +
    in-flight ``/slow``/``/stream`` + a burst of brand-new connections during the
    drain. SIGHUP is sent first (reload), then SIGTERM (drain+exit). Returns an
    artifact-schema-compatible sample row with a nested ``drain`` block.
    """
    if port is None:
        port = _free_port()
    cmd = _server_command(
        port, workers=workers, worker_mode=worker_mode, shutdown_timeout=shutdown_timeout
    )
    print(f"  Starting pounce ({DRAIN_APP}, mode={worker_mode}, {workers} workers)...")
    proc = subprocess.Popen(
        cmd,
        cwd=_REPO_ROOT,
        env=_server_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    inflight_results: dict[str, bytes] = {}
    new_conn_results: list[bytes] = []
    orphan_pids: list[int] = []
    drain_duration_s = 0.0
    stdout = b""
    stderr = b""

    try:
        if not _wait_for_probe(port):
            if proc.poll() is not None:
                _, stderr = proc.communicate()
                print(f"  Server exited early: {stderr.decode()}", file=sys.stderr)
            raise RuntimeError("drain probe server did not start")

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=slow_requests + burst + 2)

        slow_futures = [
            executor.submit(_probe_request, port, "/slow", timeout=15.0)
            for _ in range(slow_requests)
        ]
        stream_future = executor.submit(_probe_request, port, "/stream", timeout=15.0)

        stop_keepalive = False

        def _keepalive_loop() -> None:
            ka = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ka.settimeout(2.0)
            try:
                ka.connect(("127.0.0.1", port))
                while not stop_keepalive:
                    try:
                        ka.sendall(
                            b"GET /fast HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                            b"Connection: keep-alive\r\n\r\n"
                        )
                        ka.recv(4096)
                    except OSError:
                        return
                    time.sleep(0.05)
            except OSError:
                return
            finally:
                ka.close()

        ka_future = executor.submit(_keepalive_loop)

        # Let in-flight work get going, fire a reload, then the drain signal.
        time.sleep(0.25)
        if hasattr(signal, "SIGHUP"):
            proc.send_signal(signal.SIGHUP)
            time.sleep(0.1)
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)

        # Burst of brand-new connections arriving during the drain window.
        burst_futures = [
            executor.submit(_probe_request, port, "/fast", timeout=5.0) for _ in range(burst)
        ]

        try:
            stdout, stderr = proc.communicate(timeout=shutdown_timeout + 12)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=3)
        drain_duration_s = time.monotonic() - t0

        stop_keepalive = True
        # Keep-alive teardown is best-effort; the loop exits on its own socket error.
        with contextlib.suppress(Exception):
            ka_future.result(timeout=5)

        # _probe_request only raises socket errors / timeouts; an empty body is
        # recorded as a non-completion so summarize_drain counts it correctly.
        for i, fut in enumerate(slow_futures):
            try:
                inflight_results[f"slow{i}"] = fut.result(timeout=10)
            except _CONNECT_ERRORS:
                inflight_results[f"slow{i}"] = b""
        try:
            inflight_results["stream"] = stream_future.result(timeout=10)
        except _CONNECT_ERRORS:
            inflight_results["stream"] = b""

        for fut in burst_futures:
            try:
                new_conn_results.append(fut.result(timeout=6))
            except TimeoutError:
                new_conn_results.append(_HUNG)
            except (ConnectionError, OSError):  # fmt: skip
                new_conn_results.append(_REFUSED)

        orphan_pids = _child_pids(proc.pid)
        executor.shutdown(wait=False)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3)

    drain = summarize_drain(
        inflight_results=inflight_results,
        inflight_expected=slow_requests + 1,
        new_conn_results=new_conn_results,
        drain_duration_s=drain_duration_s,
        shutdown_timeout=shutdown_timeout,
        returncode=proc.returncode,
        orphan_pids=orphan_pids,
    )
    drain["traceback_in_output"] = b"Traceback" in (stdout + stderr)
    return drain_sample(drain, worker_mode=worker_mode, workers=workers, sample_index=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pounce reload/drain-under-load profile")
    parser.add_argument(
        "--worker-mode",
        default="async",
        choices=["async", "sync", "subinterpreter", "process"],
        help="Execution mode driven through the CLI (default: async)",
    )
    parser.add_argument("--workers", type=int, default=2, help="Pounce worker count")
    parser.add_argument(
        "--slow-requests", type=int, default=4, help="In-flight /slow requests during drain"
    )
    parser.add_argument(
        "--burst", type=int, default=10, help="Brand-new connections fired during drain"
    )
    parser.add_argument(
        "--shutdown-timeout", type=int, default=3, help="Server --shutdown-timeout (seconds)"
    )
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the profile N times")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: free port)")
    parser.add_argument(
        "--artifact-output", type=str, default=None, help="Save artifact-schema JSON"
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    print("Pounce Reload/Drain-Under-Load Profile")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")

    samples: list[dict[str, Any]] = []
    for sample_index in range(1, args.repeat + 1):
        print(f"\n{'=' * 60}")
        print(f"Sample {sample_index}/{args.repeat} (mode={args.worker_mode})")
        print(f"{'=' * 60}")
        sample = run_drain_profile(
            worker_mode=args.worker_mode,
            workers=args.workers,
            slow_requests=args.slow_requests,
            burst=args.burst,
            shutdown_timeout=args.shutdown_timeout,
            port=args.port,
        )
        sample["sample_index"] = sample_index
        samples.append(sample)
        d = sample["drain"]
        print(
            f"  inflight={d['inflight_completed']}/{d['inflight_expected']} "
            f"refusals={d['refusals']}/{d['new_connections']} "
            f"drops={d['silent_drops']} drain={d['drain_duration_s']}s "
            f"orphans={d['orphan_workers']} clean={d['clean_drain']}"
        )

    if args.artifact_output:
        artifact = build_profile_artifact(
            profile=DRAIN_WORKLOAD,
            command=[sys.executable, *sys.argv],
            server_command={
                f"pounce:{args.worker_mode}": _command_string(
                    _server_command(
                        args.port or 0,
                        workers=args.workers,
                        worker_mode=args.worker_mode,
                        shutdown_timeout=args.shutdown_timeout,
                    )
                )
            },
            samples=samples,
            workers=args.workers,
            duration=args.shutdown_timeout,
            connections=args.burst,
            threads=0,
            load_tool="drain_profile.py",
            load_tool_version="in-process SIGHUP+SIGTERM drain driver",
            worker_mode=args.worker_mode,
        )
        save_artifact(artifact, Path(args.artifact_output))


if __name__ == "__main__":
    main()
