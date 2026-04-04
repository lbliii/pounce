"""
TLS context creation for pounce.

Creates and configures ``ssl.SSLContext`` from ``ServerConfig`` fields.
Uses stdlib ``ssl`` with secure defaults (TLSv1.2+, no compression,
cipher order honoured).  Optionally uses ``truststore`` (via
``pounce[tls]``) for system certificate store integration.

ALPN protocols are advertised so HTTP/2 negotiation works when the h2
protocol handler is available.

"""

import logging
import ssl

from pounce._errors import TLSError
from pounce.config import ServerConfig

logger = logging.getLogger("pounce.net.tls")

# Optional truststore support (pounce[tls] extra)
try:
    import truststore  # type: ignore[import-untyped]

    _HAS_TRUSTSTORE = True
except ImportError:
    _HAS_TRUSTSTORE = False


def create_tls_context(config: ServerConfig) -> ssl.SSLContext:
    """Build an ``ssl.SSLContext`` from server configuration.

    Args:
        config: Server configuration with ``ssl_certfile`` and
            ``ssl_keyfile`` set.

    Returns:
        A configured SSLContext ready for ``asyncio.start_server(ssl=...)``.

    Raises:
        TLSError: If the certificate or key file cannot be loaded.

    """
    if not config.ssl_certfile:
        raise TLSError("ssl_certfile is required for TLS")

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

        # Secure defaults
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.options |= ssl.OP_NO_COMPRESSION
        ctx.set_ciphers("DEFAULT:!aNULL:!eNULL:!MD5")

        # Load certificate chain
        ctx.load_cert_chain(
            certfile=config.ssl_certfile,
            keyfile=config.ssl_keyfile,
        )

        # ALPN: advertise supported application protocols
        # HTTP/2 requires ALPN negotiation via TLS
        alpn_protocols = _build_alpn_protocols()
        ctx.set_alpn_protocols(alpn_protocols)
        logger.debug("ALPN protocols: %s", ", ".join(alpn_protocols))

        # Use system trust store if truststore is installed
        if _HAS_TRUSTSTORE:
            truststore.inject_into_ssl()
            logger.debug("Using system trust store via truststore")

    except ssl.SSLError as exc:
        raise TLSError(f"Failed to configure TLS: {exc}") from exc
    except FileNotFoundError as exc:
        raise TLSError(f"TLS certificate/key not found: {exc}") from exc
    except PermissionError as exc:
        raise TLSError(f"Permission denied reading TLS files: {exc}") from exc

    return ctx


def _build_alpn_protocols() -> list[str]:
    """Return the ALPN protocol list based on available optional deps.

    If h2 is installed, advertise ``h2`` first (preferred), then
    ``http/1.1`` as fallback.  Otherwise only ``http/1.1``.

    """
    protocols: list[str] = []

    # Check if h2 is available
    try:
        import h2  # noqa: F401

        protocols.append("h2")
    except ImportError:
        pass

    protocols.append("http/1.1")
    return protocols


def is_tls_configured(config: ServerConfig) -> bool:
    """Return True if TLS is configured in the server config."""
    return config.ssl_certfile is not None
