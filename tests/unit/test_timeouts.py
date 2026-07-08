"""Contract tests for request-input and response-output deadlines."""

import asyncio
from typing import Any, cast

import pytest

from pounce._errors import RequestTimeoutError
from pounce._timeouts import drain_with_timeout, receive_with_timeout

pytestmark = pytest.mark.issue(242)


async def test_receive_timeout_has_stable_request_body_code() -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    with pytest.raises(RequestTimeoutError) as caught:
        await receive_with_timeout(queue, 0.001)

    assert caught.value.code == "POUNCE_TIMEOUT_REQUEST_BODY"


async def test_h3_receive_uses_request_body_timeout() -> None:
    from pounce.asgi.h3_bridge import create_h3_receive

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    receive = create_h3_receive(queue, timeout=0.001)

    with pytest.raises(RequestTimeoutError) as caught:
        await receive()

    assert caught.value.code == "POUNCE_TIMEOUT_REQUEST_BODY"


async def test_write_timeout_closes_slow_peer_with_stable_code() -> None:
    class SlowWriter:
        closed = False

        async def drain(self) -> None:
            await asyncio.Event().wait()

        def close(self) -> None:
            self.closed = True

    writer = SlowWriter()
    with pytest.raises(RequestTimeoutError) as caught:
        await drain_with_timeout(cast("asyncio.StreamWriter", writer), 0.001)

    assert caught.value.code == "POUNCE_TIMEOUT_WRITE"
    assert writer.closed is True
