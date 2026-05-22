"""Missing-extra diagnostics for optional protocol support."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from pounce._h3_handler import create_zoomies_datagram_protocol_factory
from pounce.config import ServerConfig
from pounce.protocols.h2 import H2Connection
from pounce.protocols.ws import WSProtocol


async def _noop_app(scope, receive, send) -> None:
    pass


def test_h2_connection_missing_extra_has_install_hint() -> None:
    with (
        patch("pounce.protocols.h2._HAS_H2", False),
        pytest.raises(RuntimeError, match=r"pip install bengal-pounce\[h2\]"),
    ):
        H2Connection()


def test_websocket_protocol_missing_extra_has_install_hint() -> None:
    with (
        patch("pounce.protocols.ws._HAS_WSPROTO", False),
        pytest.raises(RuntimeError, match=r"pip install bengal-pounce\[ws\]"),
    ):
        WSProtocol()


def test_h3_factory_missing_extra_has_install_hint() -> None:
    with (
        patch("pounce._h3_handler.is_h3_available", return_value=False),
        pytest.raises(RuntimeError, match=r"pip install bengal-pounce\[h3\]"),
    ):
        create_zoomies_datagram_protocol_factory(
            _noop_app,
            ServerConfig(),
            logging.getLogger("pounce.tests.h3"),
            ("127.0.0.1", 443),
            quic_config=object(),
        )
