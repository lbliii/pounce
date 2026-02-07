"""
Protocol handlers for HTTP/1.1, HTTP/2, and WebSocket.

Each protocol module translates between raw bytes on the wire and ASGI
scope/receive/send messages. Protocol handlers are async-library-agnostic —
they accept callbacks and never import asyncio directly.

Modules:
- h1: HTTP/1.1 via h11 (phase 1)
- h2: HTTP/2 via h2 library (phase 3)
- ws: WebSocket via wsproto (phase 3)

"""
