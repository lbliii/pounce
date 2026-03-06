"""
SSE stress test — validates streaming connections hold open without memory leak.

Opens 100 concurrent SSE connections to a pounce worker, holds them open
for 10 seconds while reading events, then verifies:
1. All connections stayed open and received events.
2. RSS growth was bounded (no memory leak).

Marked with ``@pytest.mark.benchmark`` — run via ``poe bench`` or
``pytest -m benchmark``.

"""

import asyncio
import platform
import resource
import socket
import threading
import time

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.worker import Worker

# ---------------------------------------------------------------------------
# SSE app (inlined — same as conftest but self-contained for benchmark)
# ---------------------------------------------------------------------------


async def _sse_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that streams SSE events every 50ms."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"cache-control", b"no-cache"),
                (b"connection", b"keep-alive"),
            ],
        }
    )

    tick = 0
    try:
        while True:
            chunk = f"data: tick {tick}\n\n".encode()
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": True,
                }
            )
            tick += 1
            await asyncio.sleep(0.05)
    except asyncio.CancelledError, ConnectionError, OSError:
        pass

    await send(
        {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SSE_REQUEST = b"GET /events HTTP/1.1\r\nHost: localhost\r\nAccept: text/event-stream\r\n\r\n"


def _get_rss_mb() -> float:
    """Return the current process RSS in megabytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = usage.ru_maxrss
    if platform.system() == "Darwin":
        return rss / (1024 * 1024)
    return rss / 1024


def _create_socket() -> socket.socket:
    """Create a bound, listening, non-blocking socket on an ephemeral port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    sock.setblocking(False)
    return sock


async def _hold_sse_connection(
    addr: tuple[str, int],
    duration: float,
) -> int:
    """Open an SSE connection, read events for *duration* seconds.

    Returns the number of SSE events received.
    """
    event_count = 0
    try:
        reader, writer = await asyncio.open_connection(addr[0], addr[1])
        writer.write(_SSE_REQUEST)
        await writer.drain()

        deadline = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < deadline:
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            except TimeoutError:
                continue
            if not data:
                break
            # Count SSE events (each "data: ..." line is one event)
            event_count += data.count(b"data: ")

        writer.close()
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass
    return event_count


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

_NUM_CONNECTIONS = 100
_HOLD_DURATION = 10.0  # seconds
_RSS_GROWTH_LIMIT_MB = 50.0  # generous ceiling for CI environments


@pytest.mark.benchmark
@pytest.mark.timeout(60)
def test_sse_stress_no_leak() -> None:
    """Hold 100 concurrent SSE streams for 10s — verify no memory leak."""
    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        access_log=False,
        compression=False,
        max_connections=500,
    )
    sock = _create_socket()
    addr = sock.getsockname()
    shutdown = threading.Event()

    # Start 2 workers to distribute SSE connections
    workers: list[Worker] = []
    threads: list[threading.Thread] = []
    for i in range(2):
        worker = Worker(
            config,
            _sse_app,
            sock,
            worker_id=i,
            shutdown_event=shutdown,
            max_connections=250,
        )
        workers.append(worker)
        t = threading.Thread(target=worker.run, daemon=True)
        threads.append(t)
        t.start()

    time.sleep(0.3)

    rss_before = _get_rss_mb()

    # Open 100 concurrent SSE connections and hold for 10s
    async def _run() -> list[int]:
        tasks = [
            asyncio.create_task(_hold_sse_connection(addr, _HOLD_DURATION))
            for _ in range(_NUM_CONNECTIONS)
        ]
        return await asyncio.gather(*tasks)

    results = asyncio.run(_run())

    rss_after = _get_rss_mb()
    rss_growth = rss_after - rss_before

    # Shut down
    shutdown.set()
    for t in threads:
        t.join(timeout=5)
    sock.close()

    # --- Assertions ---
    connected = sum(1 for r in results if r > 0)
    total_events = sum(results)

    print(
        f"\n  [SSE stress] {connected}/{_NUM_CONNECTIONS} connections received events"
        f"\n  [SSE stress] {total_events} total events across all connections"
        f"\n  [SSE stress] RSS before={rss_before:.1f}MB, after={rss_after:.1f}MB, "
        f"growth={rss_growth:.1f}MB"
    )

    # At least 80% of connections should have received events
    # (some may fail to connect under load in CI)
    min_connected = int(_NUM_CONNECTIONS * 0.8)
    assert connected >= min_connected, (
        f"Only {connected}/{_NUM_CONNECTIONS} connections received events "
        f"(expected >= {min_connected})"
    )

    # Each connected stream should have received at least a few events
    # (10s at 50ms interval = ~200 events, but be conservative for CI)
    assert total_events > connected * 5, (
        f"Too few events: {total_events} total across {connected} connections"
    )

    # Memory growth should be bounded
    assert rss_growth < _RSS_GROWTH_LIMIT_MB, (
        f"RSS grew {rss_growth:.1f}MB during SSE stress test (limit: {_RSS_GROWTH_LIMIT_MB}MB)"
    )
