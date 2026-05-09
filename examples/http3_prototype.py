"""
HTTP/3 example using pounce with zoomies.

Runs a minimal HTTP/3 server using pounce's zoomies integration.

Requirements:
    pip install bengal-pounce[h3]

Usage:
    python examples/http3_prototype.py

Test with Chrome:
    google-chrome --enable-quic --origin-to-force-quic-on=localhost:4433 https://localhost:4433

NOTE: Requires a TLS certificate. Generate a self-signed cert:
    openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes

"""

import asyncio
import logging
import socket

try:
    from zoomies.core import QuicConfiguration

    from pounce._h3_handler import create_zoomies_datagram_protocol_factory
    from pounce.config import ServerConfig

    ZOOMIES_AVAILABLE = True
except ImportError:
    ZOOMIES_AVAILABLE = False


async def _app(scope: dict, receive: object, send: object) -> None:
    """Minimal ASGI app for HTTP/3 example."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"server", b"pounce-h3-zoomies"),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"Hello from HTTP/3 (zoomies)!\n",
        }
    )


async def main() -> None:
    """Run the HTTP/3 example server."""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("h3.example")

    if not ZOOMIES_AVAILABLE:
        logger.error("zoomies not installed. Install with: pip install bengal-pounce[h3]")
        return

    try:
        with open("cert.pem", "rb") as f:
            cert_bytes = f.read()
        with open("key.pem", "rb") as f:
            key_bytes = f.read()
    except FileNotFoundError:
        logger.error(
            "TLS certificate not found. Generate with:\n"
            "  openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes"
        )
        return

    config = ServerConfig(
        host="0.0.0.0",
        port=4433,
        ssl_certfile="cert.pem",
        ssl_keyfile="key.pem",
    )

    quic_config = QuicConfiguration(
        certificate=cert_bytes,
        private_key=key_bytes,
        idle_timeout=30.0,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 4433))

    protocol_factory = create_zoomies_datagram_protocol_factory(
        _app,
        config,
        logger,
        ("0.0.0.0", 4433),
        quic_config,
    )

    transport, _protocol = await asyncio.get_running_loop().create_datagram_endpoint(
        protocol_factory,
        sock=sock,
    )

    logger.info("HTTP/3 server on https://localhost:4433")
    logger.info(
        "Test: google-chrome --enable-quic --origin-to-force-quic-on=localhost:4433 https://localhost:4433"
    )

    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down HTTP/3 server")
