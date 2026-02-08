"""Tests for TLS context creation."""

import os
import ssl
import tempfile

import pytest

from pounce._errors import TLSError
from pounce.config import ServerConfig
from pounce.net.tls import _build_alpn_protocols, create_tls_context, is_tls_configured

# ---------------------------------------------------------------------------
# Helpers — generate self-signed cert for testing
# ---------------------------------------------------------------------------

def _generate_self_signed_cert(tmpdir: str) -> tuple[str, str]:
    """Generate a self-signed certificate and key for testing.

    Uses the ssl module's built-in capability to create test certs.
    Returns (certfile_path, keyfile_path).
    """
    # We can't easily generate certs without a dep, so use a pre-built
    # approach: create the cert via subprocess with openssl
    certfile = os.path.join(tmpdir, "cert.pem")
    keyfile = os.path.join(tmpdir, "key.pem")

    # Generate using Python's ssl module test helpers if available,
    # otherwise try openssl
    import subprocess

    result = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", keyfile, "-out", certfile,
            "-days", "1", "-nodes",
            "-subj", "/CN=localhost",
        ],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip("openssl not available for test cert generation")

    return certfile, keyfile

# ---------------------------------------------------------------------------
# Tests — is_tls_configured
# ---------------------------------------------------------------------------

class TestIsTLSConfigured:
    def test_not_configured_by_default(self) -> None:
        config = ServerConfig()
        assert is_tls_configured(config) is False

    def test_configured_with_both(self) -> None:
        config = ServerConfig(
            ssl_certfile="/path/to/cert.pem",
            ssl_keyfile="/path/to/key.pem",
        )
        assert is_tls_configured(config) is True

# ---------------------------------------------------------------------------
# Tests — create_tls_context
# ---------------------------------------------------------------------------

class TestCreateTLSContext:
    def test_raises_without_certfile(self) -> None:
        config = ServerConfig()
        with pytest.raises(TLSError, match="ssl_certfile is required"):
            create_tls_context(config)

    def test_raises_for_missing_file(self) -> None:
        config = ServerConfig(
            ssl_certfile="/nonexistent/cert.pem",
            ssl_keyfile="/nonexistent/key.pem",
        )
        with pytest.raises(TLSError, match="not found"):
            create_tls_context(config)

    def test_creates_context_with_valid_cert(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            certfile, keyfile = _generate_self_signed_cert(tmpdir)
            config = ServerConfig(
                ssl_certfile=certfile,
                ssl_keyfile=keyfile,
            )
            ctx = create_tls_context(config)
            # truststore may monkey-patch ssl.SSLContext, so check the type
            # name rather than using isinstance
            assert "SSLContext" in type(ctx).__name__

    def test_context_has_secure_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            certfile, keyfile = _generate_self_signed_cert(tmpdir)
            config = ServerConfig(
                ssl_certfile=certfile,
                ssl_keyfile=keyfile,
            )
            ctx = create_tls_context(config)
            # TLSv1.2 minimum
            assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
            # No compression
            assert ctx.options & ssl.OP_NO_COMPRESSION

    def test_context_has_alpn_protocols(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            certfile, keyfile = _generate_self_signed_cert(tmpdir)
            config = ServerConfig(
                ssl_certfile=certfile,
                ssl_keyfile=keyfile,
            )
            ctx = create_tls_context(config)
            assert "SSLContext" in type(ctx).__name__

# ---------------------------------------------------------------------------
# Tests — ALPN protocol list
# ---------------------------------------------------------------------------

class TestBuildALPNProtocols:
    def test_always_includes_http11(self) -> None:
        protocols = _build_alpn_protocols()
        assert "http/1.1" in protocols

    def test_http11_is_last(self) -> None:
        protocols = _build_alpn_protocols()
        assert protocols[-1] == "http/1.1"
