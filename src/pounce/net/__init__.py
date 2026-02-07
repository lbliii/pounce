"""
Network layer — socket binding, listening, and TLS.

Handles the lowest level of the server: binding to addresses, accepting
connections, and optionally terminating TLS.

Modules:
- listener: Socket bind, SO_REUSEPORT, accept loop
- tls: TLS context creation and ALPN negotiation

"""

from pounce.net.listener import create_listener, create_listeners
from pounce.net.tls import create_tls_context, is_tls_configured

__all__ = [
    "create_listener",
    "create_listeners",
    "create_tls_context",
    "is_tls_configured",
]
