"""
ASGI protocol bridge.

Translates between pounce's internal connection handling and the ASGI 3.0
protocol that applications expect (scope, receive, send).

Modules:
- bridge: Constructs ASGI scope/receive/send for each request
- lifespan: ASGI lifespan protocol (startup/shutdown events)

"""
