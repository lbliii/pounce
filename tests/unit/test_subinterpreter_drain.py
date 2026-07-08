"""Tests for the subinterpreter IIC bridge drain behaviour (issue #103).

These drive the *real* ``_iic_bridge`` coroutine against a fake Worker and
fake IIC queues so we can prove the bridge:

1. Always sets ``_async_shutdown`` on ``CMD_SHUTDOWN`` (even queued right
   after ``CMD_DRAIN``).
2. Bounds the drain wait by ``config.shutdown_timeout`` and shuts down on
   expiry even when the worker never becomes idle (long-lived connection).
3. Announces ``STATUS_IDLE`` once when it does become idle, and still honours
   a later ``CMD_SHUTDOWN``.

No real subinterpreters are spawned — only the bridge logic is under test.
"""

from __future__ import annotations

import asyncio
import queue
from dataclasses import dataclass, field

import pytest

from pounce._subinterpreter_bootstrap import (
    CMD_DRAIN,
    CMD_RELOAD_DRAIN,
    CMD_SHUTDOWN,
    STATUS_DRAINING,
    STATUS_IDLE,
    _iic_bridge,
    _run_worker_draining_hook,
)


class _FakeQueue:
    """Minimal stand-in for an IIC queue: get_nowait raises queue.Empty."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()

    def put(self, item: object) -> None:
        self._q.put(item)

    def get_nowait(self) -> object:
        return self._q.get_nowait()

    def drain_all(self) -> list:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out


@dataclass
class _FakeServer:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


@dataclass
class _FakeConfig:
    shutdown_timeout: float = 0.3


@dataclass
class _FakeWorker:
    """Mimics the Worker surface the bridge touches."""

    _config: _FakeConfig = field(default_factory=_FakeConfig)
    _draining: bool = False
    idle: bool = True
    _async_shutdown: asyncio.Event = field(default_factory=asyncio.Event)

    def is_idle(self) -> bool:
        return self.idle


@pytest.mark.asyncio
async def test_drain_then_shutdown_sets_async_shutdown() -> None:
    """drain then shutdown queued back-to-back -> _async_shutdown is set."""
    worker = _FakeWorker()
    worker.idle = True
    ctrl = _FakeQueue()
    status = _FakeQueue()

    ctrl.put((CMD_DRAIN,))
    ctrl.put((CMD_SHUTDOWN,))

    await asyncio.wait_for(_iic_bridge(worker, ctrl, status), timeout=2.0)

    assert worker._async_shutdown.is_set()
    assert worker._draining is True
    statuses = [m[0] for m in status.drain_all()]
    assert STATUS_DRAINING in statuses


@pytest.mark.asyncio
async def test_reload_drain_closes_old_acceptor_before_announcing_idle() -> None:
    """Reload drain retires only the old generation's accept socket."""
    worker = _FakeWorker()
    ctrl = _FakeQueue()
    status = _FakeQueue()
    server = _FakeServer()

    ctrl.put((CMD_RELOAD_DRAIN,))
    ctrl.put((CMD_SHUTDOWN,))

    await asyncio.wait_for(_iic_bridge(worker, ctrl, status, server), timeout=2.0)

    assert server.closed is True
    assert worker._draining is True
    assert worker._async_shutdown.is_set()


@pytest.mark.asyncio
async def test_draining_hook_carries_reload_generation_identity() -> None:
    scopes: list[dict] = []

    async def app(scope, receive, send) -> None:
        scopes.append(scope)

    worker = _FakeWorker()
    worker._app = app
    worker._worker_id = 3
    worker._generation = 8

    await _run_worker_draining_hook(worker, "reload")

    assert scopes == [
        {
            "type": "pounce.worker.draining",
            "worker_id": 3,
            "generation": 8,
            "reason": "reload",
            "timeout": worker._config.shutdown_timeout,
        }
    ]


@pytest.mark.asyncio
async def test_drain_non_idle_bounded_by_shutdown_timeout() -> None:
    """A never-idle worker still shuts down within ~shutdown_timeout (deadline)."""
    worker = _FakeWorker(_FakeConfig(shutdown_timeout=0.3))
    worker.idle = False  # long-lived connection: never idle
    ctrl = _FakeQueue()
    status = _FakeQueue()

    ctrl.put((CMD_DRAIN,))
    # No shutdown is ever queued; the deadline must drive completion.

    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.wait_for(_iic_bridge(worker, ctrl, status), timeout=2.0)
    elapsed = loop.time() - start

    assert worker._async_shutdown.is_set()
    # Bounded: finishes near the deadline, well under the 2s safety timeout.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_drain_then_idle_announces_idle_once_then_shutdown() -> None:
    """drain -> worker becomes idle -> STATUS_IDLE once -> later shutdown observed."""
    worker = _FakeWorker(_FakeConfig(shutdown_timeout=5.0))
    worker.idle = False
    ctrl = _FakeQueue()
    status = _FakeQueue()

    ctrl.put((CMD_DRAIN,))

    bridge = asyncio.create_task(_iic_bridge(worker, ctrl, status))

    # Let the bridge start draining, then let the worker go idle.
    await asyncio.sleep(0.15)
    worker.idle = True

    # Wait until STATUS_IDLE shows up.
    idle_seen = False
    for _ in range(40):
        await asyncio.sleep(0.05)
        if any(m[0] == STATUS_IDLE for m in status.drain_all()):
            idle_seen = True
            break
    assert idle_seen, "bridge never announced STATUS_IDLE after becoming idle"
    assert not worker._async_shutdown.is_set(), "must wait for explicit shutdown"

    # Now send the explicit shutdown; the bridge must finish.
    ctrl.put((CMD_SHUTDOWN,))
    await asyncio.wait_for(bridge, timeout=2.0)
    assert worker._async_shutdown.is_set()


@pytest.mark.asyncio
async def test_drain_then_shutdown_observed_while_never_idle() -> None:
    """#103 regression: a queued shutdown after drain is honoured even if the
    worker NEVER goes idle.

    The pre-fix bridge blocked in a ``while not is_idle()`` spin after draining,
    so the ``CMD_SHUTDOWN`` the supervisor queues right behind ``CMD_DRAIN`` was
    never read and the subinterpreter thread wedged. The fixed bridge keeps
    polling ctrl_queue while draining, so the shutdown is observed promptly —
    well before the (long) shutdown_timeout deadline could mask the hang.

    With a 5s shutdown_timeout, a bridge that only exits on the deadline would
    blow the 2s wait_for; only a bridge that reads the queued shutdown finishes
    in time.
    """
    worker = _FakeWorker(_FakeConfig(shutdown_timeout=5.0))
    worker.idle = False  # long-lived connection: is_idle() never returns True
    ctrl = _FakeQueue()
    status = _FakeQueue()

    ctrl.put((CMD_DRAIN,))
    ctrl.put((CMD_SHUTDOWN,))

    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.wait_for(_iic_bridge(worker, ctrl, status), timeout=2.0)
    elapsed = loop.time() - start

    assert worker._async_shutdown.is_set()
    assert worker._draining is True
    # Must finish via the queued shutdown, NOT the 5s deadline (and not wedge).
    assert elapsed < 1.0, f"bridge ignored queued shutdown while draining ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_shutdown_only_sets_async_shutdown_immediately() -> None:
    """A bare CMD_SHUTDOWN (no drain) sets _async_shutdown and returns."""
    worker = _FakeWorker()
    ctrl = _FakeQueue()
    status = _FakeQueue()
    ctrl.put((CMD_SHUTDOWN,))

    await asyncio.wait_for(_iic_bridge(worker, ctrl, status), timeout=2.0)
    assert worker._async_shutdown.is_set()
