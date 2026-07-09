"""Subprocess signal-path proof for the pounce CLI server."""

from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _has_subinterpreters() -> bool:
    try:
        import concurrent.interpreters  # noqa: F401
    except ImportError:
        return False
    return True


def _purge_pyc(source: Path) -> None:
    """Delete the bytecode cache for *source* so the next import recompiles.

    Belt-and-suspenders alongside PYTHONDONTWRITEBYTECODE: removes any .pyc the
    reload test could otherwise pick up after a same-length VERSION edit (#104).
    """
    cached = importlib.util.cache_from_source(str(source))
    with suppress(OSError):
        os.unlink(cached)


def _version_body(response: bytes) -> bytes:
    """Return the body (after the header terminator) of an HTTP response."""
    _, sep, body = response.partition(b"\r\n\r\n")
    return body if sep else response


def _probe_request_retry(
    port: int, path: str, *, attempts: int = 10, timeout: float = 2.0
) -> bytes:
    """Probe *path*, retrying past benign reload-window connection resets.

    A fresh connection that lands exactly on the old->new generation accept
    handover can be RST/closed with no bytes; that is a transient artifact, not
    a real outage. Retry a bounded number of times and return the first real
    (non-empty) response, so the steady-state assertion proves serving without
    masking a persistent failure.
    """
    last = b""
    for _ in range(attempts):
        try:
            resp = _probe_request(port, path, timeout=timeout)
        except ConnectionError, OSError, TimeoutError:
            time.sleep(0.1)
            continue
        if resp:
            return resp
        last = resp
        time.sleep(0.1)
    return last


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
    # Determinism for the SIGHUP reload proof (#104): never read or write a
    # bytecode cache in the spawned server. CPython validates .pyc files by
    # (source mtime-seconds, source size); a same-length edit (e.g. v1->v2)
    # within one mtime tick can leave a stale .pyc that the subinterpreter
    # reimport loads instead of the new source. Disabling the cache removes
    # that race entirely so the reload always re-executes the edited source.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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


def _start_cli_server(
    *, workers: int, worker_mode: str = "async"
) -> tuple[subprocess.Popen[bytes], int]:
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
            worker_mode,
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


def _stop_cli_server(proc: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    if proc.poll() is None:
        proc.terminate()
    try:
        return proc.communicate(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=2)
        pytest.fail("pounce subprocess did not exit after SIGTERM")


@contextmanager
def _running_cli_server(*, workers: int) -> Iterator[tuple[subprocess.Popen[bytes], int]]:
    proc, port = _start_cli_server(workers=workers)
    try:
        _wait_for_hello(port)
        yield proc, port
    finally:
        stdout, stderr = _stop_cli_server(proc)
        assert b"Traceback" not in stdout + stderr


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


@pytest.mark.integration
@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="SIGHUP is POSIX-only")
@pytest.mark.skipif(not _has_subinterpreters(), reason="subinterpreters unavailable")
def test_cli_sighup_reload_path_recovers_serving() -> None:
    """SIGHUP should keep the CLI process alive and return to serving traffic."""
    proc, port = _start_cli_server(workers=2, worker_mode="subinterpreter")
    try:
        assert b"Hello, World!" in _wait_for_hello(port)
        proc.send_signal(signal.SIGHUP)
        deadline = time.monotonic() + 8
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"pounce subprocess exited during SIGHUP reload: {proc.returncode}")
            try:
                response = _request(port)
            except (ConnectionError, OSError) as exc:
                last_error = exc
                time.sleep(0.05)
                continue
            if b"Hello, World!" in response:
                return
            time.sleep(0.05)

        msg = "pounce did not serve traffic after SIGHUP reload"
        if last_error is not None:
            raise RuntimeError(msg) from last_error
        raise RuntimeError(msg)
    finally:
        stdout, stderr = _stop_cli_server(proc)
        assert b"Traceback" not in stdout + stderr


# ---------------------------------------------------------------------------
# #104: mixed-traffic real-signal drain-under-load proof across worker modes.
#
# Locally (GIL build) we can drive: async, subinterpreter, process. The sync
# execution path only runs in thread mode on free-threaded 3.14t, so the
# ``sync`` parameter is xfail-marked here and proven by the CI 3.14t lane.
# ---------------------------------------------------------------------------


def _start_probe_server(
    *, workers: int, worker_mode: str, shutdown_timeout: int = 3
) -> tuple[subprocess.Popen[bytes], int]:
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pounce",
            "serve",
            "--app",
            "benchmarks.apps.drain_probe:app",
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
        ],
        cwd=ROOT,
        env=_server_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc, port


def _start_shutdown_order_server(
    log_path: Path,
    *,
    workers: int,
    worker_mode: str,
) -> tuple[subprocess.Popen[bytes], int]:
    port = _free_port()
    env = _server_env()
    env["POUNCE_SHUTDOWN_ORDER_LOG"] = str(log_path)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pounce",
            "serve",
            "--app",
            "tests.shutdown_order_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--worker-mode",
            worker_mode,
            "--shutdown-timeout",
            "3",
            "--no-access-log",
            "--signage",
            "off",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc, port


def _wait_for_order_event(log_path: Path, event: str, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.exists() and event in log_path.read_text().splitlines():
            return
        time.sleep(0.02)
    raise RuntimeError(f"shutdown-order probe did not record {event!r}")


def _probe_request(port: int, path: str, *, timeout: float = 8.0) -> bytes:
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


def _wait_for_probe(port: int, *, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if b"fast-ok" in _probe_request(port, "/fast", timeout=1.0):
                return
        except ConnectionError, OSError, TimeoutError:
            time.sleep(0.1)
    raise RuntimeError(f"drain_probe server did not come up within {timeout}s")


def _child_pids(pid: int) -> list[int]:
    """Return direct child PIDs of *pid* via pgrep (empty if none / unsupported)."""
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return []
    return [int(line) for line in out.stdout.split() if line.strip().isdigit()]


# Sentinels recorded per burst attempt so the classifier can tell a CLEAN
# refusal (connection refused / reset with no bytes) apart from a HUNG attempt
# (accepted then never answered -> read timeout). The old code collapsed both a
# timeout and a refusal to b"" and counted them as "clean", which made the
# bounded-503 assertions tautological: a server that silently dropped every new
# connection would still have passed. We now treat a hang as a hard failure.
_REFUSED = b"\x00REFUSED"  # connection refused / reset, no bytes -> clean
_HUNG = b"\x00HUNG"  # accepted but no response within timeout -> silent drop


class _BurstTally:
    """Counts of brand-new-connection outcomes during drain."""

    __slots__ = ("clean_close", "garbage", "hung", "served_200", "with_503")

    def __init__(self) -> None:
        self.with_503 = 0  # explicit clean 503 refusal
        self.clean_close = 0  # connection refused/reset with no bytes
        self.served_200 = 0  # raced in before drain, served normally
        self.hung = 0  # accepted then never answered (silent drop) — BAD
        self.garbage = 0  # partial/garbled bytes — BAD

    @property
    def total(self) -> int:
        return self.with_503 + self.clean_close + self.served_200 + self.hung + self.garbage

    @property
    def bad(self) -> int:
        return self.hung + self.garbage

    @property
    def refusals(self) -> int:
        """Connections cleanly refused (503 or no-byte close) — not served."""
        return self.with_503 + self.clean_close


def _classify_new_connection_results(results: list[bytes]) -> _BurstTally:
    """Tally brand-new-connection outcomes, distinguishing a hang from a refusal."""
    tally = _BurstTally()
    for r in results:
        if r == _HUNG:
            tally.hung += 1
        elif r in (b"", _REFUSED):
            # Connection refused / reset / closed with no bytes — a clean refusal.
            tally.clean_close += 1
        elif b" 503 " in r or b"503 Service Unavailable" in r:
            tally.with_503 += 1
        elif b" 200 " in r:
            # Served normally (raced in before drain) — acceptable, not a drop.
            tally.served_200 += 1
        else:
            tally.garbage += 1
    return tally


def _is_free_threaded() -> bool:
    """True on a free-threaded (nogil) interpreter where thread workers run."""
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    if is_gil_enabled is None:
        return False
    return not is_gil_enabled()


# ``--worker-mode`` selects the EXECUTION mode; the spawn mode (thread vs
# process vs subinterpreter) is auto-detected from the GIL state. On a GIL
# build, ``async`` with workers>1 runs as forked PROCESS workers — so the
# ``async`` case below exercises the process/fork drain path locally. The
# ``sync`` execution path only activates in thread mode on free-threaded
# 3.14t, so it is skipped on GIL builds and proven by the CI 3.14t lane.
_DRAIN_MODES = [
    pytest.param("async", id="async"),
    pytest.param(
        "subinterpreter",
        id="subinterpreter",
        marks=pytest.mark.skipif(not _has_subinterpreters(), reason="subinterpreters unavailable"),
    ),
    pytest.param(
        "sync",
        id="sync",
        marks=pytest.mark.skipif(
            not _is_free_threaded(),
            reason="sync execution needs thread mode (free-threaded 3.14t); "
            "CI's 3.14t lane proves this path",
        ),
    ),
]

_SHUTDOWN_ORDER_MODES = [
    pytest.param(1, "async", id="single"),
    pytest.param(2, "async", id="async-or-process"),
    pytest.param(
        2,
        "subinterpreter",
        id="subinterpreter",
        marks=pytest.mark.skipif(
            not _has_subinterpreters(),
            reason="subinterpreters unavailable",
        ),
    ),
    pytest.param(
        2,
        "sync",
        id="sync",
        marks=pytest.mark.skipif(
            not _is_free_threaded(),
            reason="sync execution needs thread mode (free-threaded 3.14t); "
            "CI's 3.14t lane proves this path",
        ),
    ),
]


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.timeout(60)
@pytest.mark.parametrize(("workers", "worker_mode"), _SHUTDOWN_ORDER_MODES)
def test_sigterm_runs_lifespan_shutdown_after_inflight_completion(
    tmp_path: Path,
    workers: int,
    worker_mode: str,
) -> None:
    """SIGTERM drains an active request before lifespan.shutdown (#249)."""
    order_log = tmp_path / "shutdown-order.log"
    proc, port = _start_shutdown_order_server(
        order_log,
        workers=workers,
        worker_mode=worker_mode,
    )
    try:
        _wait_for_probe(port)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            slow = executor.submit(_probe_request, port, "/slow", timeout=10.0)
            _wait_for_order_event(order_log, "request.start")
            proc.send_signal(signal.SIGTERM)
            response = slow.result(timeout=10.0)
        stdout, stderr = proc.communicate(timeout=15.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3)

    events = order_log.read_text().splitlines()
    assert b" 200 " in response
    assert b"slow-done" in response
    assert events.index("request.start") < events.index("request.complete")
    assert events.index("request.complete") < events.index("lifespan.shutdown")
    assert proc.returncode == 0
    assert b"Traceback" not in stdout + stderr


@pytest.mark.integration
@pytest.mark.issue(301)
@pytest.mark.slow
@pytest.mark.timeout(60)
@pytest.mark.parametrize("worker_mode", _DRAIN_MODES)
def test_sigterm_drains_under_mixed_load(worker_mode: str) -> None:
    """Real SIGTERM under short+slow+streaming+keep-alive load.

    Asserts the four contract properties:
      1. in-flight /slow + /stream requests complete fully,
      2. brand-new connections during drain get a *bounded* count of clean
         refusals (503 for async, clean close/refused for sync/subinterpreter),
         none hang or return garbage,
      3. the process exits within shutdown_timeout (+margin), returncode 0,
      4. no orphan child processes survive and the listener FD is released.
    """
    workers = 2
    shutdown_timeout = 3
    proc, port = _start_probe_server(
        workers=workers, worker_mode=worker_mode, shutdown_timeout=shutdown_timeout
    )
    inflight_results: dict[str, bytes] = {}
    new_conn_results: list[bytes] = []
    try:
        _wait_for_probe(port)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=12)

        # In-flight load: several /slow requests + one /stream consumer.
        slow_futures = [
            executor.submit(_probe_request, port, "/slow", timeout=10.0) for _ in range(4)
        ]
        stream_future = executor.submit(_probe_request, port, "/stream", timeout=10.0)

        # Keep-alive client looping /fast in the background until the server dies.
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

        # Let the in-flight requests get going, then fire the real signal.
        time.sleep(0.25)
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)

        # Burst of brand-new connections arriving during drain.
        burst = [executor.submit(_probe_request, port, "/fast", timeout=3.0) for _ in range(10)]

        # (3) Process must exit within shutdown_timeout + margin.
        try:
            stdout, stderr = proc.communicate(timeout=shutdown_timeout + 12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=3)
            pytest.fail(f"{worker_mode}: process did not exit after SIGTERM under load")
        exit_elapsed = time.monotonic() - t0

        stop_keepalive = True
        ka_future.result(timeout=5)

        # (1) In-flight requests complete fully.
        for i, fut in enumerate(slow_futures):
            try:
                inflight_results[f"slow{i}"] = fut.result(timeout=10)
            except Exception as exc:
                pytest.fail(f"{worker_mode}: in-flight /slow{i} failed: {exc!r}")
        try:
            inflight_results["stream"] = stream_future.result(timeout=10)
        except Exception as exc:
            pytest.fail(f"{worker_mode}: in-flight /stream failed: {exc!r}")

        for i in range(len(slow_futures)):
            body = inflight_results[f"slow{i}"]
            assert b" 200 " in body, f"{worker_mode}: in-flight /slow{i} not 200: {body[:80]!r}"
            assert b"slow-done" in body, (
                f"{worker_mode}: in-flight /slow{i} body incomplete: {body[:80]!r}"
            )
        stream_body = inflight_results["stream"]
        assert b" 200 " in stream_body, (
            f"{worker_mode}: in-flight /stream not 200: {stream_body[:80]!r}"
        )
        assert b"chunk-2" in stream_body, (
            f"{worker_mode}: in-flight /stream body incomplete: {stream_body[:80]!r}"
        )

        # (2) New connections during drain. Distinguish a CLEAN refusal
        # (connection refused/reset, or an explicit 503) from a HUNG attempt
        # (accepted then never answered -> read timeout) — the latter is a
        # silent drop and a hard failure.
        for fut in burst:
            try:
                new_conn_results.append(fut.result(timeout=5))
            except TimeoutError:
                # No response within the per-request socket timeout (the inner
                # socket recv raised; concurrent.futures.TimeoutError is the same
                # builtin from 3.11+): the server accepted the connection and
                # then dropped it silently.
                new_conn_results.append(_HUNG)
            except ConnectionError, OSError:
                # Refused/reset before any bytes — a clean, bounded refusal.
                new_conn_results.append(_REFUSED)
            except Exception:
                new_conn_results.append(b"GARBAGE")
        tally = _classify_new_connection_results(new_conn_results)

        # Every burst attempt is accounted for (no attempt lost in collection).
        assert tally.total == len(burst), f"{worker_mode}: lost burst results {tally.total}"
        # REAL upper bound on bad outcomes: a draining server may refuse a new
        # connection (503 / clean close) or even serve it if it raced in, but it
        # must NEVER hang it or emit garbage. This fails if even one new
        # connection is silently dropped — the case the old tautology missed.
        assert tally.bad == 0, (
            f"{worker_mode}: {tally.hung} hung + {tally.garbage} garbage new "
            f"connection(s) during drain (silent drop) — outcomes={new_conn_results!r}"
        )
        # In-flight completion is the DOMINANT, guaranteed outcome: all K slow
        # plus the streaming request returned a complete 200 (asserted above).
        # Count it explicitly here so the proof is non-tautological.
        inflight_200 = sum(1 for k in inflight_results if b" 200 " in inflight_results[k])
        expected_inflight = len(slow_futures) + 1  # slow* + stream
        assert inflight_200 == expected_inflight, (
            f"{worker_mode}: only {inflight_200}/{expected_inflight} in-flight "
            f"requests completed with 200"
        )
        # Every burst connection got a CLEAN outcome — refused (reset/close),
        # answered with a bounded 503, or served 200 if it raced in. With
        # tally.bad == 0 already asserted, this proves there is NO silent drop
        # hiding among the refusals (the gap the old `clean_503 < len(burst)+1`
        # / `clean_503 >= 0` tautologies left open).
        assert tally.with_503 + tally.clean_close + tally.served_200 == len(burst), (
            f"{worker_mode}: unclassified burst outcome — "
            f"503={tally.with_503} close={tally.clean_close} 200={tally.served_200} "
            f"hung={tally.hung} garbage={tally.garbage}"
        )
        # Refusals are bounded by the burst size by construction; the meaningful
        # bound is that NONE of them is a hang (asserted via tally.bad == 0).
        assert tally.refusals <= len(burst)

        # (3) bounded exit time + clean return code.
        assert proc.returncode == 0, f"{worker_mode}: non-zero exit {proc.returncode}"
        assert exit_elapsed < shutdown_timeout + 12

        # (4) No orphans: no surviving child processes, no Traceback.
        survivors = _child_pids(proc.pid)
        assert survivors == [], f"{worker_mode}: orphaned child processes {survivors}"
        assert b"Traceback" not in stdout + stderr, f"{worker_mode}: traceback in output"

        executor.shutdown(wait=False)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3)

    # FD/listener release: the port must be bindable again after exit.
    rebind = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rebind.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        rebind.bind(("127.0.0.1", port))
    finally:
        rebind.close()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.timeout(90)
@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="SIGHUP is POSIX-only")
@pytest.mark.skipif(not _has_subinterpreters(), reason="subinterpreters unavailable")
def test_sighup_reload_under_load_serves_new_code() -> None:
    """SIGHUP under streaming + keep-alive load reloads to the new app (#102).

    Rewrites drain_probe's VERSION on disk, fires SIGHUP at a multi-worker
    server while a /stream consumer and a keep-alive client are active, then
    asserts /version reflects the reimported module and the process keeps
    serving — proving no streaming split-brain on reload.

    Runs in subinterpreter mode (graceful_reload spawns a fresh generation
    that reimports the app by path, so on-disk code changes take effect — the
    same no-split-brain property the #102 thread+sync AsyncPool rebuild gives;
    the sync path is proven on the CI 3.14t lane). Single-worker SIGHUP is a
    documented no-op (server.py).
    """
    probe = ROOT / "benchmarks" / "apps" / "drain_probe.py"
    # Self-heal: a hard-killed prior run could have left the on-disk VERSION
    # mutated. Normalize back to the committed ``VERSION = "v1"`` baseline and
    # purge any bytecode cache so the spawned server starts from clean source.
    baseline = probe.read_text()
    if 'VERSION = "v1"' not in baseline:
        # A prior run died mid-test with a unique sentinel still on disk.
        # Reset the VERSION assignment (always its own line) to the baseline.
        baseline = re.sub(r'^VERSION = ".*"$', 'VERSION = "v1"', baseline, flags=re.MULTILINE)
        probe.write_text(baseline)
    original = baseline
    assert 'VERSION = "v1"' in original
    _purge_pyc(probe)

    # Determinism (#104): use a UNIQUE sentinel of a DIFFERENT byte length per
    # run. A same-length swap (v1->v2) within one filesystem mtime tick can let
    # CPython treat a stale .pyc (validated by mtime-seconds + size) as current,
    # so the subinterpreter reimport loads old bytecode. A longer, unique marker
    # changes the source size — defeating size-based cache validation too — and
    # cannot collide with a leftover marker from an earlier run. Combined with
    # PYTHONDONTWRITEBYTECODE in _server_env this makes the reload deterministic.
    new_version = f"v2-reload-{uuid.uuid4().hex}"
    assert len(new_version) != len("v1")  # size differs -> .pyc size check fails

    proc, port = _start_probe_server(workers=2, worker_mode="subinterpreter", shutdown_timeout=3)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    stop_keepalive = False
    try:
        _wait_for_probe(port)
        # Exact baseline body — guards against a stale .pyc serving the sentinel
        # before we have even edited the source.
        assert _version_body(_probe_request(port, "/version")) == b"v1"

        # Active streaming + keep-alive load across the reload.
        stream_future = executor.submit(_probe_request, port, "/stream", timeout=10.0)

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

        executor.submit(_keepalive_loop)

        # Mutate the module on disk (unique sentinel) so the reimport picks up
        # new code; purge any .pyc first so even a write of an existing cache is
        # impossible.
        _purge_pyc(probe)
        marker = new_version.encode("ascii")
        probe.write_text(
            re.sub(r'^VERSION = ".*"$', f'VERSION = "{new_version}"', original, flags=re.MULTILINE)
        )
        proc.send_signal(signal.SIGHUP)

        # Wait for the reloaded generation to serve the unique sentinel.
        #
        # A brand-new connection opened just as the old generation tears down
        # its asyncio server and the new generation binds can be RST by the
        # kernel (old listener closed before the SYN is accepted). That is a
        # benign accept-handover artifact, NOT a dropped in-flight request —
        # proven by retrying: the very next connection to the new generation
        # succeeds. So treat a reset/closed fresh connection as "not yet" and
        # keep polling within the deadline rather than failing the test.
        deadline = time.monotonic() + 30
        served_new = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"server exited during SIGHUP reload: {proc.returncode}")
            try:
                resp = _probe_request(port, "/version", timeout=2.0)
            except ConnectionError, OSError, TimeoutError:
                # Benign reload-window reset/refusal — the new gen is not ready
                # on THIS connection yet; the next attempt will land on it.
                time.sleep(0.1)
                continue
            if marker in resp:
                served_new = True
                break
            time.sleep(0.1)
        assert served_new, "SIGHUP reload did not serve the reimported VERSION"

        # The in-flight stream started pre-reload must still complete fully.
        stream_body = stream_future.result(timeout=10)
        assert b" 200 " in stream_body, (
            f"in-flight /stream not 200 across reload: {stream_body[:80]!r}"
        )
        assert b"chunk-2" in stream_body, (
            f"in-flight /stream body incomplete across reload: {stream_body[:80]!r}"
        )

        # Still serving normal traffic after reload. Once served_new is True the
        # new generation owns the listener, but a single fresh connection can
        # still race a tail-end handover reset; retry briefly to prove steady
        # state without masking a real outage.
        assert b"fast-ok" in _probe_request_retry(port, "/fast")
    finally:
        stop_keepalive = True
        probe.write_text(original)
        _purge_pyc(probe)
        executor.shutdown(wait=False)
        if proc.poll() is None:
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=8)
                assert b"Traceback" not in stdout + stderr
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=3)
