"""Tests for HTTP/1.1 client disconnect detection.

Covers:
- Worker._monitor_disconnect() — sets disconnect event on EOF/error.
- Integration: streaming ASGI app is cancelled when client disconnects.
"""

import asyncio
import contextlib

import pytest

from pounce.worker import Worker


class _FakeStreamReader:
    """Fake asyncio.StreamReader that simulates client disconnect."""

    def __init__(self, *, disconnect_after: int = 0, raise_error: bool = False) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after
        self._raise_error = raise_error

    async def read(self, n: int) -> bytes:
        self._calls += 1
        if self._raise_error:
            raise ConnectionResetError("Connection reset by peer")
        if self._calls > self._disconnect_after:
            return b""  # EOF — client disconnected
        return b"x"  # Simulate unexpected data


class TestMonitorDisconnect:
    """Worker._monitor_disconnect() detects client disconnect via reader."""

    @pytest.mark.asyncio
    async def test_sets_event_on_eof(self):
        """Disconnect event is set when reader returns empty bytes (EOF)."""
        reader = _FakeStreamReader(disconnect_after=0)
        disconnect = asyncio.Event()

        await Worker._monitor_disconnect(reader, disconnect)  # type: ignore[arg-type]

        assert disconnect.is_set()

    @pytest.mark.asyncio
    async def test_sets_event_on_connection_error(self):
        """Disconnect event is set when reader raises ConnectionError."""
        reader = _FakeStreamReader(raise_error=True)
        disconnect = asyncio.Event()

        await Worker._monitor_disconnect(reader, disconnect)  # type: ignore[arg-type]

        assert disconnect.is_set()

    @pytest.mark.asyncio
    async def test_reads_until_eof(self):
        """Monitor reads from socket until EOF arrives."""
        reader = _FakeStreamReader(disconnect_after=3)
        disconnect = asyncio.Event()

        await Worker._monitor_disconnect(reader, disconnect)  # type: ignore[arg-type]

        assert disconnect.is_set()
        # Should have read 3 times with data + 1 time getting EOF
        assert reader._calls == 4

    @pytest.mark.asyncio
    async def test_event_not_set_initially(self):
        """Disconnect event is not set before monitor completes."""
        disconnect = asyncio.Event()
        assert not disconnect.is_set()


class TestStreamingDisconnect:
    """Integration: streaming ASGI app is cancelled on client disconnect."""

    @pytest.mark.asyncio
    async def test_streaming_app_cancelled_on_disconnect(self):
        """A streaming ASGI app is cancelled when the client disconnects.

        Simulates the SSE pattern: an ASGI app that monitors receive()
        for http.disconnect while producing events via send().
        """
        from pounce.asgi.bridge import create_disconnect_receive

        app_started = asyncio.Event()
        app_finished = asyncio.Event()
        disconnect_received = asyncio.Event()

        disconnect = asyncio.Event()
        receive = create_disconnect_receive(disconnect)

        async def streaming_app(scope: dict, receive_fn, send_fn) -> None:
            """Simulates an SSE app that monitors for disconnect."""
            app_started.set()

            # Monitor task: wait for http.disconnect
            async def monitor() -> None:
                # First call returns empty body
                await receive_fn()
                # Second call waits for disconnect
                msg = await receive_fn()
                if msg["type"] == "http.disconnect":
                    disconnect_received.set()

            # Producer task: keep "sending" events
            async def produce() -> None:
                while True:
                    await asyncio.sleep(0.01)

            monitor_task = asyncio.create_task(monitor())
            producer_task = asyncio.create_task(produce())

            try:
                _done, pending = await asyncio.wait(
                    {monitor_task, producer_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            finally:
                app_finished.set()

        # Start the app
        app_task = asyncio.create_task(
            streaming_app({}, receive, None)
        )

        # Wait for app to start
        await asyncio.wait_for(app_started.wait(), timeout=1.0)

        # Simulate client disconnect
        disconnect.set()

        # App should finish promptly
        await asyncio.wait_for(app_finished.wait(), timeout=1.0)
        await asyncio.wait_for(app_task, timeout=1.0)

        assert disconnect_received.is_set()

    @pytest.mark.asyncio
    async def test_non_streaming_app_unaffected(self):
        """A non-streaming app that calls receive() once works normally."""
        from pounce.asgi.bridge import create_disconnect_receive

        disconnect = asyncio.Event()
        receive = create_disconnect_receive(disconnect)

        result = []

        async def simple_app(scope: dict, receive_fn, send_fn) -> None:
            msg = await receive_fn()
            result.append(msg["type"])
            # App returns without checking for disconnect — normal case

        await simple_app({}, receive, None)
        assert result == ["http.request"]
        # Disconnect event not set — that's fine, app exited normally
        assert not disconnect.is_set()
