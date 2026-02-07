"""
Minimal ASGI hello-world app for benchmarking pounce.

Re-exports the canonical example from ``examples/hello.py``.

Usage:
    # Single worker
    pounce benchmarks.hello_app:app

    # Multi-worker (auto-detect)
    pounce benchmarks.hello_app:app --workers 0

    # Then benchmark with:
    wrk -t4 -c100 -d10s http://127.0.0.1:8000/
    # or
    hey -n 10000 -c 100 http://127.0.0.1:8000/

"""

from examples.hello import app

__all__ = ["app"]
