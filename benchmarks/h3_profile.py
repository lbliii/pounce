#!/usr/bin/env python3
"""Real-CLI HTTP/3 throughput and latency profile for Pounce (#240).

The profile uses ``bengal-zoomies`` as a sans-I/O QUIC client, drives repeated
requests over persistent HTTP/3 connections, and can emit a governed benchmark
artifact. It is a local protocol snapshot, not a cross-protocol comparison.

Usage::

    python benchmarks/h3_profile.py --connections 4 --duration 5 --repeat 5 \
        --artifact-output benchmarks/artifacts/<date>/http3-local.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime
import json
import math
import os
import platform
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from benchmarks.run_benchmark import (  # noqa: E402 - path bootstrap must precede import
    _command_string,
    _TelemetrySampler,
    build_profile_artifact,
    save_artifact,
)

H3_APP = "benchmarks.apps.hello:app"
H3_WORKLOAD = "http3_hello"
_REQUEST_TIMEOUT = 5.0


def _generate_certificate(directory: Path) -> tuple[Path, Path]:
    """Generate a one-day localhost certificate for the benchmark server."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _server_command(port: int, workers: int, cert_path: str, key_path: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pounce",
        "serve",
        "--app",
        H3_APP,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--worker-mode",
        "async",
        "--ssl-certfile",
        cert_path,
        "--ssl-keyfile",
        key_path,
        "--http3",
        "--no-access-log",
        "--signage",
        "off",
    ]


def _artifact_server_command(port: int, workers: int) -> list[str]:
    command = _server_command(port, workers, "<generated-cert.pem>", "<generated-key.pem>")
    command[0] = "python"
    return command


def _server_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = f"{_REPO_ROOT}{os.pathsep}{Path(_REPO_ROOT) / 'src'}"
    if env.get("PYTHONPATH"):
        pythonpath = f"{pythonpath}{os.pathsep}{env['PYTHONPATH']}"
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _free_port() -> int:
    """Find a localhost port available to both TCP and UDP."""
    for _ in range(20):
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            tcp.bind(("127.0.0.1", 0))
            port = int(tcp.getsockname()[1])
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                udp.bind(("127.0.0.1", port))
            except OSError:
                continue
            finally:
                udp.close()
            return port
        finally:
            tcp.close()
    raise RuntimeError("could not find a port free for both TCP and UDP")


class _H3Client:
    """Small blocking HTTP/3 client built directly on zoomies sans-I/O."""

    def __init__(self, server_addr: tuple[str, int]) -> None:
        from zoomies.core import QuicConfiguration, QuicConnection
        from zoomies.h3 import H3Connection

        self._server_addr = server_addr
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._quic = QuicConnection(
            QuicConfiguration(is_client=True, verify_mode=False, server_name="localhost")
        )
        self._h3 = H3Connection(sender=self._quic)
        self._stream_id = 0

    def _flush(self) -> None:
        for datagram in self._quic.send_datagrams(now=time.monotonic()):
            self._socket.sendto(datagram, self._server_addr)

    def _next_events(self, deadline: float) -> list[Any]:
        now = time.monotonic()
        timer = self._quic.get_timer()
        wait = max(0.001, deadline - now)
        if timer is not None:
            wait = min(wait, max(0.001, timer - now))
        self._socket.settimeout(wait)
        try:
            data, _addr = self._socket.recvfrom(65535)
        except TimeoutError:
            now = time.monotonic()
            timer = self._quic.get_timer()
            if timer is not None and timer <= now:
                events = self._quic.handle_timer(now)
                self._flush()
                return events
            return []
        events = self._quic.datagram_received(data, self._server_addr, now=time.monotonic())
        self._flush()
        return events

    def connect(self, *, timeout: float = _REQUEST_TIMEOUT) -> None:
        from zoomies.events import ConnectionClosed, HandshakeComplete

        self._quic.connect()
        self._flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self._next_events(deadline):
                if isinstance(event, HandshakeComplete):
                    return
                if isinstance(event, ConnectionClosed):
                    raise ConnectionError(f"QUIC handshake closed: {event.reason}")
        raise TimeoutError("QUIC handshake timed out")

    def request(self, *, timeout: float = _REQUEST_TIMEOUT) -> tuple[int, float]:
        """Send one GET and return ``(response_bytes, latency_ms)``."""
        from zoomies.events import (
            ConnectionClosed,
            H3DataReceived,
            H3HeadersReceived,
            StreamDataReceived,
            StreamReset,
        )

        stream_id = self._stream_id
        self._stream_id += 4
        started = time.perf_counter()
        self._h3.send_headers(
            stream_id,
            [
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", b"localhost"),
                (b":path", b"/"),
            ],
            end_stream=True,
        )
        self._flush()
        status: bytes | None = None
        body_bytes = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self._next_events(deadline):
                if isinstance(event, ConnectionClosed):
                    raise ConnectionError(f"QUIC connection closed: {event.reason}")
                if isinstance(event, StreamReset) and event.stream_id == stream_id:
                    raise ConnectionError(f"HTTP/3 stream reset: {event.error_code}")
                if not isinstance(event, StreamDataReceived):
                    continue
                for h3_event in self._h3.handle_event(event):
                    if h3_event.stream_id != stream_id:
                        continue
                    if isinstance(h3_event, H3HeadersReceived):
                        status = dict(h3_event.headers).get(b":status")
                        if h3_event.end_stream:
                            return self._finish_response(status, body_bytes, started)
                    elif isinstance(h3_event, H3DataReceived):
                        body_bytes += len(h3_event.data)
                        if h3_event.end_stream:
                            return self._finish_response(status, body_bytes, started)
        raise TimeoutError("HTTP/3 request timed out")

    @staticmethod
    def _finish_response(
        status: bytes | None, body_bytes: int, started: float
    ) -> tuple[int, float]:
        if status != b"200":
            raise ConnectionError(f"unexpected HTTP/3 status: {status!r}")
        return body_bytes, (time.perf_counter() - started) * 1000

    def close(self) -> None:
        self._quic.close(reason="benchmark complete")
        with contextlib.suppress(OSError):
            self._flush()
        self._socket.close()


def _wait_for_h3(server_addr: tuple[str, int], *, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "server did not answer"
    while time.monotonic() < deadline:
        client = _H3Client(server_addr)
        try:
            client.connect(timeout=1.0)
            client.request(timeout=1.0)
            return
        except (ConnectionError, OSError, TimeoutError) as exc:
            last_error = str(exc)
            time.sleep(0.1)
        finally:
            client.close()
    raise RuntimeError(f"HTTP/3 server did not become ready: {last_error}")


def _drive_connection(
    server_addr: tuple[str, int], *, start_at: float, duration: float
) -> dict[str, Any]:
    client = _H3Client(server_addr)
    latencies_ms: list[float] = []
    response_bytes = 0
    errors = 0
    try:
        client.connect()
        delay = start_at - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        deadline = start_at + duration
        while time.perf_counter() < deadline:
            try:
                body_bytes, latency_ms = client.request()
            except (ConnectionError, OSError, TimeoutError):  # fmt: skip
                errors += 1
                break
            response_bytes += body_bytes
            latencies_ms.append(latency_ms)
    except (ConnectionError, OSError, TimeoutError):  # fmt: skip
        errors += 1
    finally:
        client.close()
    return {
        "requests": len(latencies_ms),
        "errors": errors,
        "response_bytes": response_bytes,
        "latencies_ms": latencies_ms,
    }


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize_http3(results: list[dict[str, Any]], *, duration: float) -> dict[str, Any]:
    """Aggregate per-connection observations into profile metrics."""
    latencies = [float(value) for result in results for value in result["latencies_ms"]]
    total_requests = sum(int(result["requests"]) for result in results)
    errors = sum(int(result["errors"]) for result in results)
    response_bytes = sum(int(result["response_bytes"]) for result in results)
    return {
        "connections": len(results),
        "successful_connections": sum(1 for result in results if result["requests"]),
        "requests": total_requests,
        "errors": errors,
        "response_bytes": response_bytes,
        "req_per_sec": round(total_requests / duration, 2) if duration else 0.0,
        "latency_avg_ms": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "latency_p50_ms": round(_percentile(latencies, 50), 3),
        "latency_p99_ms": round(_percentile(latencies, 99), 3),
    }


def run_h3_profile(
    *, connections: int, duration: float, workers: int, port: int
) -> tuple[dict[str, Any], dict[str, str]]:
    """Run one real-CLI HTTP/3 sample and return the sample plus raw output."""
    with tempfile.TemporaryDirectory(prefix="pounce-h3-bench-") as temp_dir:
        cert_path, key_path = _generate_certificate(Path(temp_dir))
        command = _server_command(port, workers, str(cert_path), str(key_path))
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_server_env(),
            text=True,
        )
        try:
            _wait_for_h3(("127.0.0.1", port))
            start_at = time.perf_counter() + 0.25
            with (
                _TelemetrySampler(proc.pid) as sampler,
                concurrent.futures.ThreadPoolExecutor(max_workers=connections) as pool,
            ):
                futures = [
                    pool.submit(
                        _drive_connection,
                        ("127.0.0.1", port),
                        start_at=start_at,
                        duration=duration,
                    )
                    for _ in range(connections)
                ]
                results = [future.result() for future in futures]
            telemetry = sampler.result()
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()

    if proc.returncode not in (0, -signal.SIGINT):
        raise RuntimeError(f"HTTP/3 benchmark server exited {proc.returncode}: {stderr}")
    summary = summarize_http3(results, duration=duration)
    sample = {
        "server": "pounce-http3",
        "workload": H3_WORKLOAD,
        "workers": workers,
        "duration_s": duration,
        "threads": connections,
        "connections": connections,
        "req_per_sec": summary["req_per_sec"],
        "avg_latency_ms": summary["latency_avg_ms"],
        "p50_latency_ms": summary["latency_p50_ms"],
        "p99_latency_ms": summary["latency_p99_ms"],
        "transfer_per_sec": (
            f"{summary['response_bytes'] / duration:.2f}B" if duration else "0.00B"
        ),
        "total_requests": summary["requests"],
        "errors": summary["errors"],
        "peak_rss_bytes": telemetry.peak_rss_bytes,
        "cpu_percent_mean": telemetry.cpu_percent_mean,
        "cpu_percent_peak": telemetry.cpu_percent_peak,
        "worker_pids": telemetry.worker_pids,
        "http3": summary,
    }
    raw = {
        "stdout": json.dumps(summary, sort_keys=True),
        "stderr": "\n".join(part for part in (stdout.strip(), stderr.strip()) if part),
    }
    return sample, raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Pounce HTTP/3 benchmark profile")
    parser.add_argument("--connections", type=int, default=4, help="Persistent QUIC connections")
    parser.add_argument("--duration", type=float, default=5.0, help="Load duration per sample")
    parser.add_argument("--workers", type=int, default=1, help="Pounce worker count")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the profile N times")
    parser.add_argument("--port", type=int, default=0, help="Server TCP/UDP port; 0 chooses one")
    parser.add_argument(
        "--artifact-output", type=str, default=None, help="Save artifact-schema JSON"
    )
    args = parser.parse_args()
    if args.connections < 1:
        parser.error("--connections must be >= 1")
    if args.duration <= 0:
        parser.error("--duration must be > 0")
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    port = args.port or _free_port()
    print("Pounce HTTP/3 Profile")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    print(f"Port: {port} (TCP + UDP)")
    samples: list[dict[str, Any]] = []
    raw_output: list[dict[str, Any]] = []
    for sample_index in range(1, args.repeat + 1):
        print(f"\nSample {sample_index}/{args.repeat}")
        sample, raw = run_h3_profile(
            connections=args.connections,
            duration=args.duration,
            workers=args.workers,
            port=port,
        )
        sample["sample_index"] = sample_index
        samples.append(sample)
        raw_output.append(
            {
                "server": sample["server"],
                "workload": sample["workload"],
                "workers": args.workers,
                "sample_index": sample_index,
                "load_tool": "h3_profile.py",
                **raw,
            }
        )
        h3 = sample["http3"]
        print(
            f"  requests={h3['requests']} errors={h3['errors']} "
            f"rate={h3['req_per_sec']}/s p99={h3['latency_p99_ms']}ms"
        )

    if args.artifact_output:
        artifact = build_profile_artifact(
            profile=H3_WORKLOAD,
            command=["python", *sys.argv],
            server_command={
                "pounce-http3": _command_string(_artifact_server_command(port, args.workers))
            },
            samples=samples,
            workers=args.workers,
            duration=int(args.duration),
            connections=args.connections,
            threads=args.connections,
            load_tool="h3_profile.py",
            load_tool_version="bengal-zoomies sans-I/O persistent-connection driver",
            worker_mode="async+h3-thread",
            raw_output=raw_output,
            extra={
                "protocol": "HTTP/3 over QUIC/UDP",
                "tls": "ephemeral self-signed ECDSA P-256 certificate",
                "caveat": (
                    "Local protocol snapshot; no HTTP/2 comparison and no public product-level "
                    "performance claim. Re-run on the target deployment platform."
                ),
            },
        )
        output_path = Path(args.artifact_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_artifact(artifact, output_path)
        print(f"\nArtifact: {output_path}")


if __name__ == "__main__":
    main()
