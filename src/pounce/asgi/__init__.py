"""
ASGI protocol bridge.

Translates between pounce's internal connection handling and the ASGI 3.0
protocol that applications expect (scope, receive, send).

Modules:
- bridge: Constructs ASGI scope/receive/send for HTTP requests
- ws_bridge: Constructs ASGI scope/receive/send for WebSocket connections
- lifespan: ASGI lifespan protocol (startup/shutdown events)

"""

from pounce.asgi.bridge import build_scope, create_receive, create_send
from pounce.asgi.lifespan import run_lifespan
from pounce.asgi.ws_bridge import build_ws_scope, create_ws_receive, create_ws_send

__all__ = [
    "build_scope",
    "build_ws_scope",
    "create_receive",
    "create_send",
    "create_ws_receive",
    "create_ws_send",
    "run_lifespan",
]
