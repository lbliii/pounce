"""Integration tests for HTTP/3 (QUIC) support."""

import logging

import pytest


from pounce.config import ServerConfig
from pounce.protocols.h3 import is_h3_available


class TestHTTP3Config:
    """HTTP/3 config and CLI tests — no aioquic required."""

    def test_http3_config_validation(self) -> None:
        """http3_enabled requires TLS."""
        with pytest.raises(ValueError, match="http3_enabled requires"):
            ServerConfig(http3_enabled=True)

    def test_cli_http3_flag(self) -> None:
        """--http3 maps to http3_enabled."""
        from pounce._cli import _build_parser

        parser = _build_parser()
        parsed = parser.parse_args(
            [
                "app:app",
                "--http3",
                "--ssl-certfile",
                "c.pem",
                "--ssl-keyfile",
                "k.pem",
            ],
        )
        assert parsed.http3 is True


@pytest.mark.skipif(
    not is_h3_available(),
    reason="aioquic not installed; pip install pounce[h3]",
)
class TestHTTP3Integration:
    """HTTP/3 integration tests — require aioquic."""

    def test_create_h3_protocol_factory(self) -> None:
        """H3 protocol factory can be created when aioquic is available."""
        from pounce._h3_handler import create_h3_protocol_factory

        config = ServerConfig(
            host="127.0.0.1",
            port=4433,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
        )
        factory = create_h3_protocol_factory(
            lambda s, r, sn: None,
            config,
            logging.getLogger("test"),
            ("127.0.0.1", 4433),
        )
        assert factory.__name__ == "H3ServerProtocol"
        # Config with fake cert paths is fine — we only create the class
