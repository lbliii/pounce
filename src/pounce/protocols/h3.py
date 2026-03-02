"""
HTTP/3 (QUIC) protocol support — availability check for zoomies.

HTTP/3 uses QUIC (UDP) transport. The actual protocol handling lives in
``_h3_handler.py`` which integrates zoomies' sans-I/O QuicConnection.

Requires the ``h3`` optional dependency (``pip install pounce[h3]``).

"""

try:
    import zoomies  # noqa: F401

    _HAS_H3 = True
except ImportError:
    _HAS_H3 = False


def is_h3_available() -> bool:
    """Check if zoomies is installed for HTTP/3 support."""
    return _HAS_H3
