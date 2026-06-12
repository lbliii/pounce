"""Process/fork worker-mode end-to-end tests (GIL build only).

On a GIL-enabled CPython build, ``detect_worker_mode()`` returns ``"process"``
and the supervisor forks one worker PROCESS per worker (vs. threads on 3.14t).
These tests exercise that production path end-to-end through the CLI:

- fork spawn (the server has N worker child PROCESSES),
- request serving across multiple worker processes,
- graceful SIGTERM shutdown,
- no orphaned child PROCESSES survive shutdown.

The thread-mode equivalents live in ``test_multi_worker.py``; that suite's
``test_server_shutdown_no_orphaned_threads`` only inspects orphan THREADS, so
this file adds the process-PID equivalent called for by issue #105.

Gated behind the ``process`` marker AND a GIL-enabled skip so they only run on
the stock 3.14 (GIL) CI lane and locally on a GIL build — on 3.14t the worker
mode is thread, so there are no child processes to inspect.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from pounce._runtime import detect_worker_mode, is_gil_enabled

ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.process,
    pytest.mark.skipif(
        not is_gil_enabled(),
        reason="process/fork worker mode only runs on a GIL-enabled build (3.14)",
    ),
    pytest.mark.skipif(
        not hasattr(signal, "SIGTERM"),
        reason="SIGTERM is POSIX-only",
    ),
    pytest.mark.skipif(
        shutil.which("pgrep") is None,
        reason="pgrep is required to enumerate child processes",
    ),
]


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


def _child_pids(ppid: int) -> list[int]:
    """Direct child PIDs of ``ppid`` (POSIX, via pgrep -P)."""
    result = subprocess.run(
        ["pgrep", "-P", str(ppid)],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def _request(port: int, *, timeout: float = 1.0) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", port))
        sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        chunks = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)
    finally:
        sock.close()


def _wait_for_hello(port: int, *, timeout: float = 10.0) -> bytes:
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


def _wait_for_children(ppid: int, expected: int, *, timeout: float = 10.0) -> list[int]:
    """Wait until ``ppid`` has at least ``expected`` direct child processes."""
    deadline = time.monotonic() + timeout
    children: list[int] = []
    while time.monotonic() < deadline:
        children = _child_pids(ppid)
        if len(children) >= expected:
            return children
        time.sleep(0.05)
    return children


def _start_cli_server(*, workers: int) -> tuple[subprocess.Popen[bytes], int, str]:
    """Start ``pounce serve`` in its own session with N process workers.

    Output is redirected to a temp file (not a PIPE): forked worker processes
    inherit the parent's stdout, so a PIPE would deadlock ``communicate()``
    until every child closed it. ``start_new_session=True`` puts the server in
    its own process group so a single signal can reach all workers.

    Returns ``(proc, port, log_path)``; the caller deletes ``log_path``.
    """
    port = _free_port()
    log_fd, log_path = tempfile.mkstemp(prefix="pounce-proc-", suffix=".log")
    try:
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
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        os.close(log_fd)
    return proc, port, log_path


def test_runtime_selects_process_mode_under_gil() -> None:
    """On a GIL build the worker mode must be ``process`` (the path under test)."""
    assert detect_worker_mode() == "process"


def _force_kill(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort teardown: kill the whole process group, then the parent."""
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError, PermissionError:
            proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _read_log(log_path: str) -> bytes:
    return Path(log_path).read_bytes()


def test_process_mode_forks_workers_and_serves() -> None:
    """The server forks N worker PROCESSES and they serve requests."""
    workers = 3
    proc, port, log_path = _start_cli_server(workers=workers)
    try:
        assert b"Hello, World!" in _wait_for_hello(port)

        children = _wait_for_children(proc.pid, workers)
        assert len(children) >= workers, (
            f"expected >= {workers} worker child processes, got {children}"
        )

        # Serve several requests; in process mode each is handled by a forked
        # worker process. All must return 200 with the hello body.
        for _ in range(8):
            response = _request(port)
            assert b"HTTP/1.1 200" in response
            assert b"Hello, World!" in response
    finally:
        _force_kill(proc)
        with contextlib.suppress(OSError):
            os.unlink(log_path)


def test_process_mode_sigterm_drains_and_reaps_children() -> None:
    """SIGTERM exits cleanly and leaves no orphaned worker PROCESSES."""
    workers = 2
    proc, port, log_path = _start_cli_server(workers=workers)
    try:
        assert b"Hello, World!" in _wait_for_hello(port)
        children = _wait_for_children(proc.pid, workers)
        assert len(children) >= workers, f"workers did not fork: {children}"

        # Signal only the parent (the production SIGTERM path). The parent must
        # then drain and reap its forked workers itself.
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=20)
        output = _read_log(log_path)

        assert returncode == 0, f"non-zero exit: {returncode}\n{output.decode(errors='replace')}"
        assert b"Traceback" not in output

        # No worker child PROCESSES should survive — the supervisor must reap
        # every forked child. Give the OS a beat to release the PIDs.
        deadline = time.monotonic() + 5.0
        survivors = [pid for pid in children if _pid_alive(pid)]
        while survivors and time.monotonic() < deadline:
            time.sleep(0.1)
            survivors = [pid for pid in children if _pid_alive(pid)]
        assert survivors == [], f"orphaned worker processes survived shutdown: {survivors}"
    finally:
        _force_kill(proc)
        with contextlib.suppress(OSError):
            os.unlink(log_path)


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` still refers to a live (non-zombie) process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # On POSIX a reaped child becomes a zombie until the parent waits; once the
    # parent process is gone the PID is fully released. Treat zombies (parent
    # already exited) as not alive by checking the process state via ps.
    result = subprocess.run(
        ["ps", "-o", "state=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    state = result.stdout.strip()
    if not state:
        return False
    return not state.startswith("Z")
