"""
WebSocket chat room — multi-client broadcast with shared state.

The compelling free-threading showcase.  Multiple browser tabs (or
``websocat`` sessions) connect via WebSocket.  Every message is
broadcast to all other connected clients.  The shared room state is
protected by ``threading.Lock`` — the same pattern used in
``examples/lifespan.py``.

On Python 3.14t with ``--workers N``, pounce runs N worker **threads**
sharing a single interpreter.  Because the ``_Room`` instance is
module-level, clients connected to *different* worker threads can
still chat with each other — true shared-memory concurrency.

On GIL builds (multi-process workers), each process gets its own
``_Room``, so broadcast only reaches clients on the same worker.
This is expected — shared mutable state across processes requires
IPC, which is outside pounce's scope.

Prerequisites:
    pip install bengal-pounce[ws]   # installs wsproto

Run it:
    pounce examples.websocket_chat:app --workers 4

Open multiple browser tabs at http://127.0.0.1:8000/ and start
chatting.  Messages appear in all tabs instantly.

Or use websocat:
    websocat ws://127.0.0.1:8000/ws

"""

import asyncio
import contextlib
import logging
import threading
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

log = logging.getLogger("examples.websocket_chat")

# ---------------------------------------------------------------------------
# Chat room — thread-safe shared state
# ---------------------------------------------------------------------------


class _Room:
    """A set of connected WebSocket clients with broadcast capability.

    Thread-safety: the client set is protected by a ``threading.Lock``.
    Each client is an ``asyncio.Queue`` living on whatever event loop
    accepted the connection.  Broadcasting pushes messages into each
    queue using ``loop.call_soon_threadsafe`` so it works across worker
    threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[asyncio.Queue[str | None], asyncio.AbstractEventLoop] = {}

    def join(
        self,
        queue: asyncio.Queue[str | None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Add a client to the room."""
        with self._lock:
            self._clients[queue] = loop

    def leave(self, queue: asyncio.Queue[str | None]) -> None:
        """Remove a client from the room."""
        with self._lock:
            self._clients.pop(queue, None)

    def broadcast(self, message: str, *, sender: asyncio.Queue[str | None]) -> None:
        """Send *message* to every client except *sender*."""
        with self._lock:
            targets = [(q, loop) for q, loop in self._clients.items() if q is not sender]
        for queue, loop in targets:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, message)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._clients)


_room = _Room()

# ---------------------------------------------------------------------------
# Embedded HTML chat page
# ---------------------------------------------------------------------------

_HTML = b"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pounce chat</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #0f172a; color: #e2e8f0;
    display: flex; flex-direction: column;
    height: 100vh; max-width: 640px; margin: 0 auto;
    padding: 1rem;
  }
  h1 { font-size: 1.25rem; padding: 0.5rem 0; color: #38bdf8; }
  #log {
    flex: 1; overflow-y: auto;
    border: 1px solid #334155; border-radius: 0.5rem;
    padding: 0.75rem; margin-bottom: 0.75rem;
    font-size: 0.9rem; line-height: 1.5;
    background: #1e293b;
  }
  #log .system { color: #94a3b8; font-style: italic; }
  #log .self   { color: #38bdf8; }
  #log .other  { color: #e2e8f0; }
  form { display: flex; gap: 0.5rem; }
  input {
    flex: 1; padding: 0.5rem 0.75rem;
    border: 1px solid #334155; border-radius: 0.375rem;
    background: #1e293b; color: #e2e8f0;
    font-size: 0.9rem; outline: none;
  }
  input:focus { border-color: #38bdf8; }
  button {
    padding: 0.5rem 1.25rem;
    border: none; border-radius: 0.375rem;
    background: #0ea5e9; color: #fff;
    font-size: 0.9rem; cursor: pointer;
  }
  button:hover { background: #38bdf8; }
  #status { font-size: 0.75rem; color: #64748b; padding: 0.25rem 0; }
</style>
</head>
<body>
<h1>pounce chat</h1>
<div id="status">connecting&hellip;</div>
<div id="log"></div>
<form id="form">
  <input id="msg" autocomplete="off" placeholder="Type a message&hellip;" />
  <button type="submit">Send</button>
</form>
<script>
(function() {
  const log = document.getElementById("log");
  const form = document.getElementById("form");
  const input = document.getElementById("msg");
  const status = document.getElementById("status");

  function append(text, cls) {
    const div = document.createElement("div");
    div.className = cls;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(proto + "//" + location.host + "/ws");

  ws.onopen = function() {
    status.textContent = "connected";
    input.focus();
  };
  ws.onclose = function() {
    status.textContent = "disconnected";
    append("Connection closed.", "system");
  };
  ws.onmessage = function(e) {
    append(e.data, "other");
  };

  form.onsubmit = function(e) {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || ws.readyState !== WebSocket.OPEN) return;
    ws.send(text);
    append("you: " + text, "self");
    input.value = "";
  };
})();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """WebSocket chat room with broadcast.

    - ``GET /`` serves the HTML chat page.
    - ``WebSocket /ws`` (or any path) joins the chat room.
    """
    # --- Lifespan -----------------------------------------------------------
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                log.info("chat room ready")
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                log.info("chat room shutting down (%d clients)", _room.size)
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    # --- HTTP — serve the chat page -----------------------------------------
    if scope["type"] == "http":
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(_HTML)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _HTML})
        return

    # --- WebSocket — join the chat room -------------------------------------
    assert scope["type"] == "websocket"

    message = await receive()
    if message["type"] != "websocket.connect":
        return

    await send({"type": "websocket.accept"})

    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[str | None] = asyncio.Queue()
    _room.join(inbox, loop)

    log.info("client joined (%d connected)", _room.size)

    async def _relay_outbound() -> None:
        """Forward broadcast messages from the room to this client."""
        while True:
            text = await inbox.get()
            if text is None:
                return
            await send({"type": "websocket.send", "text": text})

    async def _relay_inbound() -> None:
        """Read client messages and broadcast to the room."""
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return
            if message["type"] == "websocket.receive":
                text = message.get("text")
                if text is not None:
                    _room.broadcast(text, sender=inbox)

    relay_task = asyncio.create_task(_relay_outbound())
    try:
        await _relay_inbound()
    except Exception:
        pass
    finally:
        _room.leave(inbox)
        # Unblock the relay task so it can exit.
        inbox.put_nowait(None)
        relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay_task
        log.info("client left (%d connected)", _room.size)

    await send({"type": "websocket.close", "code": 1000})
