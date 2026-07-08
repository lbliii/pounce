"""Tests for per-worker lifecycle scopes.

Covers:
- Worker sends ``pounce.worker.startup`` before accepting connections.
- Worker sends ``pounce.worker.shutdown`` after closing the server.
- Startup failure prevents the worker from accepting connections.
- Shutdown failure is logged but does not prevent worker exit.
- Apps that raise on unknown scope types are handled gracefully.
- Single-worker mode (via Server) sends worker scopes.
"""

import asyncio
import threading

import pytest

from pounce._errors import SupervisorError, WorkerError
from pounce._runtime import WorkerMode
from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.server import Server
from pounce.sync_worker import SyncWorker
from pounce.worker import Worker
from tests.conftest import send_raw_request, start_worker


class TestWorkerLifecycleScopes:
    """Worker sends per-worker startup/shutdown scopes to the ASGI app."""

    @pytest.mark.asyncio
    async def test_startup_scope_sent_before_connections(self):
        """App receives pounce.worker.startup with correct worker_id."""
        scopes_seen: list[dict] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] in ("pounce.worker.startup", "pounce.worker.shutdown"):
                scopes_seen.append(dict(scope))
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=7)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        # Give worker time to start and process the startup scope
        await asyncio.sleep(0.3)

        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

        # Startup scope should have been sent
        startup_scopes = [s for s in scopes_seen if s["type"] == "pounce.worker.startup"]
        assert len(startup_scopes) == 1
        assert startup_scopes[0]["worker_id"] == 7

    @pytest.mark.asyncio
    async def test_shutdown_scope_sent_after_close(self):
        """App receives pounce.worker.shutdown with correct worker_id."""
        scopes_seen: list[dict] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] in ("pounce.worker.startup", "pounce.worker.shutdown"):
                scopes_seen.append(dict(scope))
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=3)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        await asyncio.sleep(0.3)
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

        shutdown_scopes = [s for s in scopes_seen if s["type"] == "pounce.worker.shutdown"]
        assert len(shutdown_scopes) == 1
        assert shutdown_scopes[0]["worker_id"] == 3

    @pytest.mark.asyncio
    async def test_startup_and_shutdown_both_sent(self):
        """Both startup and shutdown scopes fire in correct order."""
        events: list[str] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                events.append("startup")
                return
            if scope["type"] == "pounce.worker.shutdown":
                events.append("shutdown")
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        await asyncio.sleep(0.3)
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

        assert events == ["startup", "shutdown"]


class TestSyncWorkerLifecycleScopes:
    """Sync workers provide the same lifecycle scopes on their runner loop."""

    def test_sync_worker_hooks_and_request_share_runner_loop(self) -> None:
        events: list[tuple[str, int, asyncio.AbstractEventLoop]] = []
        startup_seen = threading.Event()

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            scope_type = scope["type"]
            if scope_type in {"pounce.worker.startup", "pounce.worker.shutdown"}:
                events.append(
                    (
                        scope_type,
                        scope["worker_id"],
                        asyncio.get_running_loop(),
                    )
                )
                if scope_type == "pounce.worker.startup":
                    startup_seen.set()
                return
            if scope_type == "http":
                events.append(("http", 11, asyncio.get_running_loop()))
                await receive()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-length", b"2")],
                    }
                )
                await send({"type": "http.response.body", "body": b"ok"})

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        shutdown = threading.Event()
        worker = SyncWorker(
            config,
            app,
            sock,
            worker_id=11,
            shutdown_event=shutdown,
        )
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        try:
            assert startup_seen.wait(timeout=3.0)
            response = send_raw_request(
                sock.getsockname(),
                b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
            )
            assert b"HTTP/1.1 200" in response
            assert response.endswith(b"ok")
        finally:
            shutdown.set()
            thread.join(timeout=3.0)
            sock.close()

        assert not thread.is_alive()
        assert [event[0] for event in events] == [
            "pounce.worker.startup",
            "http",
            "pounce.worker.shutdown",
        ]
        assert events[0][1] == events[2][1] == 11
        assert events[0][2] is events[1][2] is events[2][2]

    def test_sync_worker_fatal_startup_failure_never_accepts(self) -> None:
        shutdown_scope_seen = False

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal shutdown_scope_seen
            if scope["type"] == "pounce.worker.startup":
                raise RuntimeError("required worker resource unavailable")
            if scope["type"] == "pounce.worker.shutdown":
                shutdown_scope_seen = True
            if scope["type"] == "http":  # pragma: no cover - must never accept
                raise AssertionError("failed sync worker must not accept requests")

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            worker_startup_failure="shutdown",
        )
        sock = create_listener(config)
        shutdown = threading.Event()
        worker = SyncWorker(
            config,
            app,
            sock,
            worker_id=12,
            shutdown_event=shutdown,
        )
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        thread.join(timeout=3.0)
        sock.close()

        assert not thread.is_alive()
        assert shutdown.is_set()
        assert shutdown_scope_seen is False


class TestWorkerStartupFailure:
    """Worker startup hook failure policy (issue #65).

    Default ('ignore') keeps generic-ASGI compatibility: a hook exception is
    logged and serving continues.  Opt-in ('shutdown') fails loud: the worker
    refuses to serve and signals the supervisor to stop.
    """

    @staticmethod
    def _lifespan(scope: Scope, receive: Receive, send: Send):
        """Return the standard lifespan coroutine for an app branch."""

        async def _run() -> None:
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

        return _run()

    def test_default_policy_continues_serving(self):
        """Default 'ignore': a failing startup hook is logged, serving continues.

        Generic ASGI apps that don't recognise pounce.worker.startup raise on
        it; the worker must still come up and serve requests.
        """

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                msg = "app does not understand this scope"
                raise RuntimeError(msg)
            if scope["type"] == "pounce.worker.shutdown":
                return
            if scope["type"] == "http":
                await receive()
                body = b"served"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-length", str(len(body)).encode())],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
            if scope["type"] == "lifespan":
                await self._lifespan(scope, receive, send)

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(app, config=config)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr, b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
            )
        finally:
            worker.shutdown()
            thread.join(timeout=3)
            sock.close()

        assert b"HTTP/1.1 200" in response
        assert b"served" in response

    def test_shutdown_policy_refuses_to_serve_and_signals_supervisor(self):
        """'shutdown': hook failure stops the worker and signals the shared
        event (how the supervisor learns); the worker never reaches its
        accept loop."""

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                msg = "cannot initialise worker resources"
                raise RuntimeError(msg)
            if scope["type"] == "http":  # pragma: no cover - must never run
                raise AssertionError("worker must not accept connections")
            if scope["type"] == "lifespan":
                await self._lifespan(scope, receive, send)

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            worker_startup_failure="shutdown",
        )
        sock = create_listener(config)
        # Mimic the supervisor's shared shutdown event.
        ext_shutdown = threading.Event()
        worker = Worker(config, app, sock, worker_id=0, shutdown_event=ext_shutdown)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        # The worker returns from _serve before asyncio.start_server, so the
        # thread exits on its own — proving it never entered the accept loop.
        thread.join(timeout=3)
        try:
            assert not thread.is_alive()  # exited (did not block on serve)
            assert ext_shutdown.is_set()  # signalled the supervisor to stop
        finally:
            sock.close()

    def test_shutdown_policy_standalone_worker_still_exits(self):
        """Fail-loud with no shared event (standalone Worker) still exits cleanly."""

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                msg = "boom"
                raise RuntimeError(msg)
            if scope["type"] == "lifespan":
                await self._lifespan(scope, receive, send)

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            worker_startup_failure="shutdown",
        )
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
        sock.close()


class TestWorkerShutdownFailure:
    """Worker shutdown hook failure is non-fatal."""

    @pytest.mark.asyncio
    async def test_shutdown_failure_non_fatal(self):
        """If shutdown scope raises, worker still exits cleanly."""
        startup_called = False

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal startup_called
            if scope["type"] == "pounce.worker.startup":
                startup_called = True
                return
            if scope["type"] == "pounce.worker.shutdown":
                msg = "Cleanup failed"
                raise RuntimeError(msg)
            if scope["type"] == "lifespan":
                while True:
                    msg_data = await receive()
                    if msg_data["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg_data["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        await asyncio.sleep(0.3)
        assert startup_called

        worker.shutdown()
        # Worker should exit despite shutdown failure
        thread.join(timeout=3)
        assert not thread.is_alive()
        sock.close()


class TestUnrecognizedScopeGraceful:
    """Apps that raise on unknown scope types don't crash the worker."""

    @pytest.mark.asyncio
    async def test_app_raises_on_unknown_scope(self):
        """App that raises TypeError on non-HTTP scopes still starts."""

        async def strict_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            if scope["type"] == "http":
                return
            # Unknown scope — raise
            msg = f"Unknown scope type: {scope['type']}"
            raise TypeError(msg)

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, strict_app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        # Worker should still be alive — startup scope exceptions are suppressed
        # so that apps which don't understand pounce.worker.startup proceed normally.
        import time

        time.sleep(0.5)
        assert thread.is_alive()
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


class TestSingleWorkerLifecycle:
    """Server single-worker mode sends worker lifecycle scopes."""

    @pytest.mark.asyncio
    async def test_single_worker_sends_both_scopes(self):
        """In single-worker mode, worker startup and shutdown scopes fire."""
        events: list[str] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                events.append("startup")
                return
            if scope["type"] == "pounce.worker.shutdown":
                events.append("shutdown")
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        server = Server(config, app)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Give the server time to start
        await asyncio.sleep(0.5)

        assert "startup" in events

        server.shutdown()
        thread.join(timeout=3)

        assert events == ["startup", "shutdown"]

    @pytest.mark.asyncio
    async def test_single_worker_ignores_unknown_startup_scope_exception(self):
        """Strict ASGI apps still start when they reject pounce.worker.startup."""

        async def strict_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            if scope["type"] == "http":
                return
            msg = f"Unknown scope type: {scope['type']}"
            raise TypeError(msg)

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        server = Server(config, strict_app)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        assert server._started_event.wait(timeout=3.0)

        server.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()

    def test_single_worker_startup_failure_shutdown_prevents_serving(self):
        """Single-worker fail-loud: a failing startup hook stops the server
        before it ever becomes ready (issue #65)."""

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                msg = "boom"
                raise RuntimeError(msg)
            if scope["type"] == "lifespan":
                while True:
                    msg_data = await receive()
                    if msg_data["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg_data["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            worker_startup_failure="shutdown",
        )
        server = Server(config, app)

        with pytest.raises(WorkerError) as exc_info:
            server.run()

        assert exc_info.value.code == "POUNCE_WORKER_STARTUP_FAILED"
        assert not server._started_event.is_set()


class TestEmbeddedSubinterpreterBoundary:
    """Embedded subinterpreter mode validates import identity before launch."""

    @staticmethod
    async def _app(scope: Scope, receive: Receive, send: Send) -> None:
        return

    @pytest.mark.issue(246)
    def test_missing_app_path_fails_at_server_construction(self) -> None:
        with pytest.raises(SupervisorError) as caught:
            Server(ServerConfig(worker_mode="subinterpreter"), self._app)

        assert caught.value.code == "POUNCE_SUPERVISOR_SUBINTERPRETER_NO_APP_PATH"
        assert "app_path" in (caught.value.hint or "")

    @pytest.mark.issue(246)
    def test_one_subinterpreter_worker_uses_supervisor_path(self, mocker) -> None:
        server = Server(
            ServerConfig(worker_mode="subinterpreter", workers=1),
            self._app,
            app_path="tests.unit.test_worker_lifecycle:TestEmbeddedSubinterpreterBoundary._app",
        )
        mocker.patch("pounce.server.configure_logging")
        mocker.patch.object(Server, "_apply_integrations", autospec=True)
        mocker.patch.object(Server, "_log_worker_mode_notice", autospec=True)
        mocker.patch.object(Server, "_print_banner", autospec=True)
        run_multi = mocker.patch.object(Server, "_run_multi", autospec=True)

        server.run()

        run_multi.assert_called_once_with(server, 1, WorkerMode.SUBINTERPRETER)
