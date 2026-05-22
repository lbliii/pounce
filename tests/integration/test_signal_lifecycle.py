"""Subprocess signal-path proof for the pounce CLI server."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _server_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = str(ROOT / "src")
    if env.get("PYTHONPATH"):
        pythonpath = f"{pythonpath}{os.pathsep}{env['PYTHONPATH']}"
    env["PYTHONPATH"] = pythonpath
    return env


def _request(port: int, *, timeout: float = 0.5) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", port))
        sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        response = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        return bytes(response)
    finally:
        sock.close()


def _wait_for_hello(port: int, *, timeout: float = 5.0) -> bytes:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = _request(port)
        except (ConnectionError, OSError) as exc:
            last_error = exc
            time.sleep(0.05)
            continue
        if b"Hello, World!" in response:
            return response
        time.sleep(0.05)
    msg = f"pounce did not return hello response within {timeout}s"
    if last_error is not None:
        raise RuntimeError(msg) from last_error
    raise RuntimeError(msg)


def _start_cli_server(*, workers: int) -> tuple[subprocess.Popen[bytes], int]:
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pounce",
            "serve",
            "--app",
            "benchmarks.apps.hello:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--worker-mode",
            "async",
            "--shutdown-timeout",
            "2",
            "--no-access-log",
            "--signage",
            "off",
        ],
        cwd=ROOT,
        env=_server_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc, port


@pytest.mark.integration
def test_cli_sigterm_drains_and_exits_cleanly() -> None:
    """SIGTERM should produce a bounded, clean CLI server shutdown."""
    proc, port = _start_cli_server(workers=1)
    try:
        assert b"Hello, World!" in _wait_for_hello(port)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=6)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=2)

    assert proc.returncode == 0
    assert b"Traceback" not in stdout + stderr
