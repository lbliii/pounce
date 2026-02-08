"""
ASGI lifespan handler.

Manages the ASGI lifespan protocol — sends startup/shutdown events to the
application and waits for completion. Used as an async context manager so
the server can bracket its run loop with lifespan events.

Handles apps that don't support lifespan — whether they raise an
exception or silently return for non-HTTP scopes.

"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from pounce._errors import LifespanError
from pounce._types import ASGIApp
from pounce.config import ServerConfig

logger = logging.getLogger("pounce.lifespan")

@asynccontextmanager
async def run_lifespan(
    app: ASGIApp,
    config: ServerConfig,
) -> AsyncIterator[None]:
    """Run the ASGI lifespan protocol as an async context manager.

    Sends lifespan.startup on entry, waits for the app to respond with
    lifespan.startup.complete, yields control to the caller, then sends
    lifespan.shutdown on exit.

    If the app doesn't support lifespan (raises an exception or returns
    silently during startup), the lifespan is treated as a no-op.

    Args:
        app: The ASGI application.
        config: Server configuration.

    Yields:
        None — the caller runs the server between startup and shutdown.

    Raises:
        LifespanError: If the app sends lifespan.startup.failed.

    """
    startup_complete = asyncio.Event()
    shutdown_complete = asyncio.Event()
    startup_failed = False
    failure_message = ""
    app_finished = asyncio.Event()

    receive_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def receive() -> dict[str, Any]:
        return await receive_queue.get()

    async def send(message: dict[str, Any]) -> None:
        nonlocal startup_failed, failure_message

        msg_type = message["type"]

        if msg_type == "lifespan.startup.complete":
            startup_complete.set()
        elif msg_type == "lifespan.startup.failed":
            startup_failed = True
            failure_message = message.get("message", "")
            startup_complete.set()  # Unblock the waiter
        elif msg_type == "lifespan.shutdown.complete":
            shutdown_complete.set()

    scope: dict[str, Any] = {
        "type": "lifespan",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
    }

    # Run the app in a background task
    async def _run_app() -> None:
        try:
            await app(scope, receive, send)
        except Exception:
            # App raised during lifespan — not unusual, many apps
            # don't implement the lifespan protocol at all.
            pass
        finally:
            # If the app returned or raised without completing startup,
            # treat it as "lifespan not supported" and unblock the server.
            # This handles frameworks (like chirp) that silently return
            # for non-HTTP scopes instead of raising.
            if not startup_complete.is_set():
                startup_complete.set()
            app_finished.set()

    task = asyncio.create_task(_run_app())

    try:
        # Send startup event
        await receive_queue.put({"type": "lifespan.startup"})

        # Wait for startup response
        await startup_complete.wait()

        if startup_failed:
            raise LifespanError(
                f"Lifespan startup failed: {failure_message}" if failure_message
                else "Lifespan startup failed"
            )

        logger.info("Lifespan startup complete")
        yield

    finally:
        # Send shutdown event
        if not app_finished.is_set():
            await receive_queue.put({"type": "lifespan.shutdown"})

            # Wait for shutdown with timeout
            try:
                await asyncio.wait_for(
                    shutdown_complete.wait(),
                    timeout=config.shutdown_timeout,
                )
                logger.info("Lifespan shutdown complete")
            except TimeoutError:
                logger.warning(
                    "Lifespan shutdown timed out after %.1fs",
                    config.shutdown_timeout,
                )

        # Cancel the app task if still running
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
