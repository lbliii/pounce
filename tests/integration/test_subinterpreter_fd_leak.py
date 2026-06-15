"""FD-leak regression tests for subinterpreter worker mode (issue #106).

Subinterpreter workers run inside daemon threads the parent cannot kill, and
each one is handed a ``os.dup``'d copy of the listener FD. If a worker stops
abnormally (a crash/respawn, or a force-stop of an old generation that never
drained) before its bootstrap closes that FD, the descriptor leaks. Over many
abnormal cycles the process exhausts its FD budget.

These tests count open FDs before/after N abnormal respawns and N graceful
reloads whose old worker is force-stopped while non-draining, asserting there
is no net FD growth. They are gated on ``has_subinterpreters()`` and run on the
local GIL venv (``concurrent.interpreters`` spawns there).
"""

import contextlib
import os
import socket
import sys
import threading
import time

import pytest

from pounce._runtime import WorkerMode, has_subinterpreters
from pounce.config import ServerConfig
from pounce.net.listener import create_listeners
from pounce.supervisor import Supervisor

pytestmark = pytest.mark.skipif(
    not has_subinterpreters(),
    reason="concurrent.interpreters not available",
)

APP_PATH = "examples.hello:app"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(host: str, port: int, path: str = "/") -> int:
    """Send a minimal HTTP/1.1 GET and return the status code."""
    with socket.create_connection((host, port), timeout=5) as conn:
        request = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        conn.sendall(request.encode())
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
    first_line = data.split(b"\r\n")[0]
    return int(first_line.split(b" ")[1])


def _wait_until_serving(host: str, port: int, deadline_s: float) -> bool:
    """Poll until a 200 is served or the deadline elapses.

    Catches connection/timeout errors so a transient window where the old
    worker has died but the respawn has not finished accepting does not fail
    the test.
    """
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            if _http_get(host, port) == 200:
                return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def _close_sockets(sockets: list[socket.socket]) -> None:
    for s in sockets:
        with contextlib.suppress(OSError):
            s.close()


def _open_fd_count() -> int:
    """Count open file descriptors for this process, portably.

    Prefers ``/proc/self/fd`` (Linux / CI's 3.14t lane), falls back to psutil
    when present, and finally to a brute-force ``os.fstat`` probe over the FD
    range (the macOS dev venv has no procfs and may lack psutil).
    """
    if sys.platform == "linux":
        with contextlib.suppress(OSError):
            return len(os.listdir("/proc/self/fd"))

    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        with contextlib.suppress(Exception):
            return psutil.Process().num_fds()

    # Brute-force fallback: probe every FD up to the soft limit.
    import resource

    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    limit = soft if 0 < soft < 65536 else 4096
    count = 0
    for fd in range(limit):
        try:
            os.fstat(fd)
        except OSError:
            continue
        count += 1
    return count


def _start_supervisor(
    config: ServerConfig, sockets: list[socket.socket]
) -> tuple[Supervisor, threading.Thread]:
    supervisor = Supervisor(
        config,
        app=None,
        mode=WorkerMode.SUBINTERPRETER,
        app_path=APP_PATH,
    )
    supervisor.set_lifespan_state({})
    sup_thread = threading.Thread(target=supervisor.run, args=(sockets,), daemon=True)
    sup_thread.start()
    return supervisor, sup_thread


class TestSubinterpreterFDLeak:
    """No net FD growth across abnormal respawns and force-stopped reloads."""

    def test_no_fd_leak_across_abnormal_respawns(self) -> None:
        """Killing the worker via IIC shutdown (simulated crash) N times must
        not leak the dup'd listener FD: the parent reclaims it on respawn."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
        )
        sockets = create_listeners(config, count=1, shared=True)
        supervisor, sup_thread = _start_supervisor(config, sockets)

        try:
            time.sleep(1.0)
            assert _http_get("127.0.0.1", port) == 200

            # Baseline taken once steady-state (1 worker) is serving.
            baseline = _open_fd_count()

            # Stay within the restart budget (_MAX_RESTARTS=5 per 60s window);
            # the FD reclaim path is identical on every cycle.
            cycles = 3
            for _ in range(cycles):
                assert len(supervisor._iic_queues) >= 1
                ctrl_queue, _ = supervisor._iic_queues[0]
                # Abnormal stop: shutdown straight away, no drain handshake.
                ctrl_queue.put(("shutdown",))
                # Wait for the health monitor (1s tick) to detect death and the
                # backoff (0.5s/1s/2s...) to elapse before the respawn serves.
                assert _wait_until_serving("127.0.0.1", port, deadline_s=15.0), (
                    "respawned worker did not resume serving"
                )

            # Allow any transient FDs from the last respawn to settle.
            time.sleep(0.5)
            after = _open_fd_count()

            # A leak would grow ~1 FD per cycle. Allow a small slack for
            # transient sockets/queues but well under the per-cycle leak.
            assert after <= baseline + 2, (
                f"FD leak across {cycles} abnormal respawns: "
                f"baseline={baseline}, after={after}"
            )
        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_no_fd_leak_across_force_stopped_reloads(self) -> None:
        """Graceful reloads whose old worker is held non-draining (so it is
        force-stopped past reload_timeout) must not leak the dup'd FD."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
            # Short reload_timeout so the old, non-draining worker is forced
            # down quickly each cycle.
            reload_timeout=1.0,
            shutdown_timeout=1.0,
        )
        sockets = create_listeners(config, count=1, shared=True)
        supervisor, sup_thread = _start_supervisor(config, sockets)

        try:
            time.sleep(1.0)
            assert _http_get("127.0.0.1", port) == 200

            baseline = _open_fd_count()
            start_gen = supervisor._generation

            cycles = 3
            for i in range(cycles):
                # Hold the current generation's worker open with a long-lived
                # connection so it never drains and must be force-stopped.
                hold = socket.create_connection(("127.0.0.1", port), timeout=5)
                hold.sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
                try:
                    reload_thread = threading.Thread(
                        target=supervisor.graceful_reload, daemon=True
                    )
                    reload_thread.start()
                    reload_thread.join(timeout=20.0)
                    assert not reload_thread.is_alive(), "reload did not complete"
                    assert supervisor._generation == start_gen + i + 1
                finally:
                    with contextlib.suppress(OSError):
                        hold.close()

                # New generation should be serving.
                time.sleep(0.5)
                assert _http_get("127.0.0.1", port) == 200

            time.sleep(0.5)
            after = _open_fd_count()

            assert after <= baseline + 2, (
                f"FD leak across {cycles} force-stopped reloads: "
                f"baseline={baseline}, after={after}"
            )
        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)
