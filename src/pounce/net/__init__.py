"""
Network layer — socket binding, listening, and TLS.

Handles the lowest level of the server: binding to addresses, accepting
connections, and optionally terminating TLS.

Modules:
- listener: Socket bind, SO_REUSEPORT, accept loop
- tls: TLS context creation (phase 3)

"""

from pounce.net.listener import create_listener

__all__ = ["create_listener"]
