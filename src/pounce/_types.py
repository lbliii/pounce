"""
ASGI type definitions for pounce.

Typed definitions for the ASGI 3.0 protocol. These replace the raw dicts and
callables from the ASGI spec with concrete types for internal use.

External ASGI apps still use the standard untyped protocol — pounce translates
at the boundary.

See Also:
- https://asgi.readthedocs.io/en/latest/specs/main.html
- chirp/_internal/asgi.py (same pattern for the framework side)

"""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

# ASGI 3.0 protocol types
type Scope = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[dict[str, Any]]]
type Send = Callable[[dict[str, Any]], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
