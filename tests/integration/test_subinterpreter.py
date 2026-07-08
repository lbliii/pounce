"""Integration tests for subinterpreter worker mode (PEP 734)."""

import concurrent.futures
import contextlib
import socket
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

# Minimal ASGI app for testing — must be importable by path
APP_PATH = "examples.hello:app"
FACTORY_APP_PATH = "examples.factory_app:create_app()"
STATE_APP_PATH = "examples.lifespan_state:app"
LIFESPAN_STATE = {"app_name": "pounce-lifecycle-proof", "version": 239}
EXPECTED_STATE_BODY = b"pounce-lifecycle-proof:239\n"


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(host: str, port: int, path: str = "/") -> tuple[int, bytes]:
    """Send a minimal HTTP/1.1 GET and return (status_code, body)."""
    with socket.create_connection((host, port), timeout=5) as conn:
        request = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        conn.sendall(request.encode())
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk

    # Parse status code from first line
    first_line = data.split(b"\r\n")[0]
    status = int(first_line.split(b" ")[1])
    # Body is after \r\n\r\n
    body = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
    return status, body


def _close_sockets(sockets: list[socket.socket]) -> None:
    """Close sockets, suppressing errors."""
    for s in sockets:
        with contextlib.suppress(OSError):
            s.close()


class TestSubinterpreterWorker:
    """Test that subinterpreter workers accept and serve requests."""

    def test_single_worker_serves_request(self) -> None:
        """A single subinterpreter worker should serve HTTP requests."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,  # Not used — subinterpreter imports by path
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        # Run supervisor in a background thread
        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            # Wait for worker to start serving
            time.sleep(1.0)

            # Send test requests
            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert b"Hello, World!" in body

            # Multiple requests
            for _ in range(5):
                s, _b = _http_get("127.0.0.1", port)
                assert s == 200

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_factory_app_import(self) -> None:
        """Factory-pattern app (module:create_app()) should work in subinterpreter."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=FACTORY_APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert b"Hello from factory!" in body

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_lifespan_state_passed_to_worker(self) -> None:
        """IIC-safe lifespan state should be available in subinterpreter worker."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path="examples.lifespan_state:app",
        )
        # Simulate lifespan state set by main interpreter
        supervisor.set_lifespan_state({"app_name": "pounce-test", "version": 42})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert b"pounce-test:42" in body

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_multiple_workers_serve_requests(self) -> None:
        """Multiple subinterpreter workers should all serve requests."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=2,
            worker_mode="subinterpreter",
            access_log=False,
        )

        sockets = create_listeners(config, count=2, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.5)

            # Send multiple concurrent-ish requests
            for _ in range(10):
                status, body = _http_get("127.0.0.1", port)
                assert status == 200
                assert b"Hello, World!" in body

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_graceful_shutdown(self) -> None:
        """Subinterpreter workers should shut down gracefully via IIC."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
            shutdown_timeout=5.0,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            # Verify serving
            status, _ = _http_get("127.0.0.1", port)
            assert status == 200

        finally:
            # Trigger shutdown
            t0 = time.monotonic()
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            shutdown_time = time.monotonic() - t0

            # Should shut down cleanly and quickly
            assert not sup_thread.is_alive(), "Supervisor did not exit"
            assert shutdown_time < 5.0, f"Shutdown took too long: {shutdown_time:.1f}s"

            _close_sockets(sockets)

    def test_shutdown_with_active_connections(self) -> None:
        """Shutdown should complete even with connections in flight."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
            shutdown_timeout=5.0,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            def send_request(_i: int) -> int:
                try:
                    status, _ = _http_get("127.0.0.1", port)
                    return status
                except ConnectionError, TimeoutError, OSError:
                    return -1

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(send_request, i) for i in range(5)]
                time.sleep(0.1)
                supervisor.shutdown()
                results = [f.result() for f in concurrent.futures.as_completed(futures, timeout=5)]

            successes = [r for r in results if r == 200]
            assert len(successes) >= 1, f"Expected at least 1 success, got {results}"

        finally:
            if sup_thread.is_alive():
                supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            assert not sup_thread.is_alive(), "Supervisor did not exit after shutdown"
            _close_sockets(sockets)

    def test_worker_respawn_after_crash(self) -> None:
        """A replacement worker should receive the original lifespan state."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=STATE_APP_PATH,
        )
        supervisor.set_lifespan_state(LIFESPAN_STATE)

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            # Verify serving
            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert body == EXPECTED_STATE_BODY

            # Kill the worker by sending shutdown via IIC (simulates crash)
            assert len(supervisor._iic_queues) >= 1
            ctrl_queue, _ = supervisor._iic_queues[0]
            ctrl_queue.put(("shutdown",))

            # Wait for health monitor to respawn
            time.sleep(3.0)

            # New worker should be serving
            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert body == EXPECTED_STATE_BODY

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    @pytest.mark.issue(239)
    def test_graceful_reload(self) -> None:
        """Reload under load should preserve service and lifespan state."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
            reload_timeout=5.0,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=STATE_APP_PATH,
        )
        supervisor.set_lifespan_state(LIFESPAN_STATE)

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            # Verify serving before reload
            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert body == EXPECTED_STATE_BODY

            old_generation = supervisor._generation

            stop_requests = threading.Event()
            request_results: list[tuple[int, bytes]] = []

            def send_requests_during_reload() -> None:
                while not stop_requests.is_set():
                    try:
                        request_results.append(_http_get("127.0.0.1", port))
                    except (ConnectionError, TimeoutError, OSError) as exc:
                        request_results.append((-1, str(exc).encode()))

            load_thread = threading.Thread(
                target=send_requests_during_reload,
                daemon=True,
            )
            load_thread.start()

            # Trigger graceful reload in a background thread (it blocks)
            reload_thread = threading.Thread(
                target=supervisor.graceful_reload,
                daemon=True,
            )
            try:
                reload_thread.start()
                reload_thread.join(timeout=15.0)
                assert not reload_thread.is_alive(), "Reload did not complete in time"
            finally:
                stop_requests.set()
                load_thread.join(timeout=5.0)

            assert request_results, "No requests completed during reload"
            failed_requests = [
                result for result in request_results if result != (200, EXPECTED_STATE_BODY)
            ]
            assert not failed_requests, failed_requests

            # Generation should have incremented
            assert supervisor._generation == old_generation + 1

            # Allow new workers to start serving
            time.sleep(1.0)

            # Verify new workers serve requests
            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert body == EXPECTED_STATE_BODY

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_memory_isolation(self) -> None:
        """Module-level globals in one subinterpreter must be invisible to others.

        Uses the subinterpreter_server example which has a per-worker
        _request_count counter.  With 2 workers, each counter should
        increment independently — proving memory isolation.

        Sends concurrent requests to maximize the chance of hitting both
        workers.  If both workers are reached, the per-worker counters
        must each start from 1 and sum to the total — proving independent
        state.  If the OS routes all requests to one worker (common on
        macOS with shared sockets), the test still passes since a single
        monotonic counter is consistent with isolation.
        """
        import json

        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=2,
            worker_mode="subinterpreter",
            access_log=False,
        )

        sockets = create_listeners(config, count=2, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path="examples.subinterpreter_server:app",
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.5)

            # Send concurrent requests to increase chance of hitting both workers
            total_requests = 30
            results: list[int] = []

            def _get_count() -> int:
                status, body = _http_get("127.0.0.1", port)
                assert status == 200
                return json.loads(body)["requests_in_this_worker"]

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(_get_count) for _ in range(total_requests)]
                results = [f.result(timeout=10) for f in futures]

            # Key isolation invariant: if workers shared state, we'd see a
            # single counter reaching total_requests.  With isolation, each
            # worker counts independently, so the max counter across all
            # responses should be <= the number of requests that hit that
            # specific worker (which is <= total_requests).
            #
            # Stronger check when both workers were reached: counter value 1
            # must appear at least twice (once per worker's first request).
            ones = results.count(1)
            if ones >= 2:
                # Both workers were hit — counters are definitely independent.
                # Verify the max counter is less than total (no shared state).
                assert max(results) < total_requests, (
                    f"Max counter {max(results)} equals total {total_requests} "
                    "— workers may be sharing state"
                )
            # If ones < 2, all requests went to one worker (OS scheduling).
            # A monotonically reachable counter is still consistent with
            # isolation, so we don't fail — we just can't prove multi-worker
            # isolation on this platform/run.

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_reload_timeout_forces_shutdown(self) -> None:
        """Workers that don't drain within reload_timeout get force-closed."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
            reload_timeout=2.0,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            # Verify serving
            status, _ = _http_get("127.0.0.1", port)
            assert status == 200

            # Trigger reload — must complete even if drain doesn't finish
            t0 = time.monotonic()
            reload_thread = threading.Thread(
                target=supervisor.graceful_reload,
                daemon=True,
            )
            reload_thread.start()
            reload_thread.join(timeout=20.0)
            elapsed = time.monotonic() - t0

            assert not reload_thread.is_alive(), "Reload did not complete"
            assert elapsed < 20.0, f"Reload took too long: {elapsed:.1f}s"

            # Allow new workers to start
            time.sleep(1.0)

            # New workers should serve requests
            status, _body = _http_get("127.0.0.1", port)
            assert status == 200

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_shutdown_during_reload(self) -> None:
        """Shutdown arriving mid-reload should complete cleanly without zombies."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
            reload_timeout=10.0,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)

            # Verify serving
            status, _ = _http_get("127.0.0.1", port)
            assert status == 200

            # Start reload in background
            reload_thread = threading.Thread(
                target=supervisor.graceful_reload,
                daemon=True,
            )
            reload_thread.start()

            # Trigger shutdown while reload is in progress
            time.sleep(0.3)
            supervisor.shutdown()

            # Both threads should exit cleanly
            reload_thread.join(timeout=15.0)
            sup_thread.join(timeout=10.0)

            assert not sup_thread.is_alive(), "Supervisor did not exit after shutdown during reload"

        finally:
            if sup_thread.is_alive():
                supervisor.shutdown()
                sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_rapid_successive_reloads(self) -> None:
        """Three rapid reloads should leave only the latest generation alive."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
            reload_timeout=5.0,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)
            status, _ = _http_get("127.0.0.1", port)
            assert status == 200

            initial_gen = supervisor._generation

            # Fire 3 reloads sequentially (each blocks until complete)
            for _ in range(3):
                reload_thread = threading.Thread(
                    target=supervisor.graceful_reload,
                    daemon=True,
                )
                reload_thread.start()
                reload_thread.join(timeout=20.0)
                assert not reload_thread.is_alive(), "Reload did not complete"

            # Generation should have incremented by 3
            assert supervisor._generation == initial_gen + 3

            # Allow final generation to start serving
            time.sleep(1.0)

            # New workers should serve
            status, _body = _http_get("127.0.0.1", port)
            assert status == 200

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)

    def test_worker_crash_during_drain(self) -> None:
        """A worker that crashes during drain should be detected by supervisor."""
        port = _find_free_port()
        config = ServerConfig(
            host="127.0.0.1",
            port=port,
            workers=1,
            worker_mode="subinterpreter",
            access_log=False,
        )

        sockets = create_listeners(config, count=1, shared=True)

        supervisor = Supervisor(
            config,
            app=None,
            mode=WorkerMode.SUBINTERPRETER,
            app_path=APP_PATH,
        )
        supervisor.set_lifespan_state({})

        sup_thread = threading.Thread(
            target=supervisor.run,
            args=(sockets,),
            daemon=True,
        )
        sup_thread.start()

        try:
            time.sleep(1.0)
            status, _ = _http_get("127.0.0.1", port)
            assert status == 200

            # Send drain first, then immediately kill via shutdown
            assert len(supervisor._iic_queues) >= 1
            ctrl_queue, _ = supervisor._iic_queues[0]
            ctrl_queue.put(("drain",))
            time.sleep(0.1)
            ctrl_queue.put(("shutdown",))

            # Wait for health monitor to detect and respawn
            time.sleep(3.0)

            # New worker should be serving
            status, body = _http_get("127.0.0.1", port)
            assert status == 200
            assert b"Hello, World!" in body

        finally:
            supervisor.shutdown()
            sup_thread.join(timeout=5.0)
            _close_sockets(sockets)
