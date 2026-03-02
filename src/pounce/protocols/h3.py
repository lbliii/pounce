"""
HTTP/3 (QUIC) protocol support — availability check for aioquic.

HTTP/3 uses QUIC (UDP) transport. The actual protocol handling lives in
``_h3_handler.py`` which integrates aioquic's QuicConnectionProtocol.

Requires the ``h3`` optional dependency (``pip install pounce[h3]``).

"""

try:
    import aioquic  # noqa: F401

    _HAS_H3 = True
except ImportError:
    _HAS_H3 = False


def is_h3_available() -> bool:
    """Check if aioquic is installed for HTTP/3 support."""
    return _HAS_H3
