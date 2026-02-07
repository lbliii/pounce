"""
SSE streaming ASGI app for benchmarking pounce.

Re-exports the canonical example from ``examples/streaming_sse.py``.

Usage:
    pounce benchmarks.sse_app:app --workers 2 --no-access-log

"""

from examples.streaming_sse import app

__all__ = ["app"]
