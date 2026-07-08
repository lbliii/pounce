"""Shared request-input and response-output timeout helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import TYPE_CHECKING

from pounce._errors import RequestTimeoutError

if TYPE_CHECKING:
    from asyncio import Queue, StreamWriter

logger = logging.getLogger("pounce.timeouts")

async def receive_with_timeout[T](queue: Queue[T], timeout: float) -> T:
    """Read one request-body event, bounded by ``request_timeout``."""
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except TimeoutError as exc:
        raise RequestTimeoutError(
            "Client did not complete the request body within request_timeout.",
            code="POUNCE_TIMEOUT_REQUEST_BODY",
            hint="Increase request_timeout only for clients that legitimately upload slowly.",
        ) from exc


async def drain_with_timeout(writer: StreamWriter, timeout: float) -> None:
    """Flush buffered response bytes, closing a pathologically slow peer."""
    await wait_for_write(writer.drain(), writer, timeout)


async def wait_for_write[T](
    operation: Awaitable[T],
    writer: StreamWriter,
    timeout: float,
) -> T:
    """Bound one response-side write operation by ``write_timeout``."""
    try:
        return await asyncio.wait_for(operation, timeout=timeout)
    except TimeoutError as exc:
        logger.warning(
            "POUNCE_TIMEOUT_WRITE: client did not accept response bytes within %.1fs; "
            "closing the connection",
            timeout,
        )
        writer.close()
        raise RequestTimeoutError(
            "Client did not accept response bytes within write_timeout.",
            code="POUNCE_TIMEOUT_WRITE",
            hint="Increase write_timeout only after checking the client and proxy path.",
        ) from exc
