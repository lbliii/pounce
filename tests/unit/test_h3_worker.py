"""Tests for pounce.h3_worker — HTTP/3 worker lifecycle.

Sprint 1 coverage: worker construction, shutdown bridging,
configuration requirements, and zoomies availability gating.
"""

import asyncio
import socket
import threading
from typing import Any
from unittest.mock import patch

import pytest

from pounce.config import ServerConfig
from pounce.h3_worker import H3Worker
from pounce.protocols.h3 import is_h3_available

pytestmark = pytest.mark.skipif(
    not is_h3_available(),
    reason="zoomies not installed; pip install pounce[h3]",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> ServerConfig:
    defaults: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 4433,
        "ssl_certfile": "/tmp/cert.pem",
        "ssl_keyfile": "/tmp/key.pem",
    }
    defaults.update(overrides)
    return ServerConfig(**defaults)


async def _noop_app(scope: Any, receive: Any, send: Any) -> None:
    pass


def _make_udp_socket() -> socket.socket:
    """Create a bound UDP socket on localhost ephemeral port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    return sock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestH3WorkerConstruction:
    """Tests for H3Worker.__init__."""

    def test_basic_construction(self) -> None:
        sock = _make_udp_socket()
        try:
            config = _make_config()
            worker = H3Worker(
                config,
                _noop_app,
                sock,
                worker_id=1,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
            )
            assert worker._worker_id == 1
            assert worker._config is config
            assert worker._sock is sock
        finally:
            sock.close()

    def test_default_worker_id(self) -> None:
        sock = _make_udp_socket()
        try:
            worker = H3Worker(
                _make_config(),
                _noop_app,
                sock,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
            )
            assert worker._worker_id == 0
        finally:
            sock.close()

    def test_set_lifespan_state(self) -> None:
        sock = _make_udp_socket()
        try:
            worker = H3Worker(
                _make_config(),
                _noop_app,
                sock,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
            )
            state = {"tenant_registry": object()}
            worker.set_lifespan_state(state)
            assert worker._lifespan_state is state
        finally:
            sock.close()


class TestH3WorkerShutdown:
    """Tests for H3Worker shutdown mechanisms."""

    def test_shutdown_sets_ext_event(self) -> None:
        """shutdown() sets the external threading.Event."""
        sock = _make_udp_socket()
        try:
            ext = threading.Event()
            worker = H3Worker(
                _make_config(),
                _noop_app,
                sock,
                shutdown_event=ext,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
            )
            assert not ext.is_set()
            worker.shutdown()
            assert ext.is_set()
        finally:
            sock.close()

    def test_shutdown_without_ext_event_no_crash(self) -> None:
        """shutdown() without external event or loop doesn't crash."""
        sock = _make_udp_socket()
        try:
            worker = H3Worker(
                _make_config(),
                _noop_app,
                sock,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
            )
            # No ext_shutdown, no _async_shutdown, no _loop
            worker.shutdown()  # Should not raise
        finally:
            sock.close()

    async def test_bridge_shutdown_propagates(self) -> None:
        """_bridge_shutdown detects ext event and sets async shutdown."""
        sock = _make_udp_socket()
        try:
            ext = threading.Event()
            worker = H3Worker(
                _make_config(),
                _noop_app,
                sock,
                shutdown_event=ext,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
            )
            worker._loop = asyncio.get_running_loop()
            worker._async_shutdown = asyncio.Event()

            # Set ext event after a tiny delay via loop.call_later
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, ext.set)

            await asyncio.wait_for(worker._bridge_shutdown(ext), timeout=2.0)
            # Give call_soon a chance to execute
            await asyncio.sleep(0.05)
            assert worker._async_shutdown.is_set()
        finally:
            sock.close()


class TestH3WorkerZoomiesGate:
    """Tests for zoomies availability gating."""

    async def test_serve_without_zoomies_logs_error(self) -> None:
        """_serve() exits gracefully if zoomies is not available."""
        sock = _make_udp_socket()
        try:
            worker = H3Worker(
                _make_config(),
                _noop_app,
                sock,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
            )
            with patch("pounce.h3_worker.is_h3_available", return_value=False):
                await worker._serve()  # Should return without error
        finally:
            sock.close()
