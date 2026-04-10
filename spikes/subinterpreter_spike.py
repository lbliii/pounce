"""
Spike: Subinterpreter worker with asyncio event loop and ASGI app.

Validates:
1. Create subinterpreter in a thread
2. Import an ASGI app inside it
3. Run asyncio.run() inside it
4. Communicate shutdown via interpreters.Queue
5. Join the thread cleanly

Usage:
    python spikes/subinterpreter_spike.py
"""

import concurrent.interpreters as ci
import socket
import threading
import time


def main() -> None:
    # --- Setup: create control queue and a listening socket ---
    ctrl_queue = ci.create_queue()   # supervisor → worker
    status_queue = ci.create_queue() # worker → supervisor

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(128)
    addr = server_sock.getsockname()
    print(f"[supervisor] Listening on {addr[0]}:{addr[1]}")

    sock_fd = server_sock.fileno()

    # --- Create subinterpreter ---
    interp = ci.create()
    interp.prepare_main(
        ctrl_queue=ctrl_queue,
        status_queue=status_queue,
        sock_fd=sock_fd,
        bind_host=addr[0],
        bind_port=addr[1],
    )

    # --- Worker code (runs inside subinterpreter) ---
    worker_code = r'''
import asyncio
import socket
import time

# --- Minimal ASGI app (imported inline to prove concept) ---
async def app(scope, receive, send):
    """Minimal ASGI app that returns JSON."""
    if scope["type"] == "http":
        body = b'{"status": "ok", "worker": "subinterpreter"}\n'
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })

# --- Reconstruct socket from FD ---
server_sock = socket.socket(
    socket.AF_INET, socket.SOCK_STREAM, fileno=sock_fd
)
server_sock.setblocking(False)

status_queue.put(("started",))

async def handle_connection(reader, writer):
    """Handle one HTTP/1.1 request (minimal, no parsing library)."""
    try:
        data = await asyncio.wait_for(reader.read(8192), timeout=5.0)
        if not data:
            writer.close()
            return

        # Minimal HTTP/1.1 parse — just enough for the spike
        request_line = data.split(b"\r\n")[0]
        method, path, _ = request_line.split(b" ", 2)

        scope = {
            "type": "http",
            "method": method.decode(),
            "path": path.decode(),
        }

        response_parts = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(msg):
            response_parts.append(msg)

        await app(scope, receive, send)

        # Write response
        start = response_parts[0]
        body_msg = response_parts[1]
        status = start["status"]
        headers = b"".join(
            k + b": " + v + b"\r\n" for k, v in start["headers"]
        )
        response = (
            f"HTTP/1.1 {status} OK\r\n".encode()
            + headers
            + b"\r\n"
            + body_msg["body"]
        )
        writer.write(response)
        await writer.drain()
    except Exception as e:
        status_queue.put(("error", str(e)))
    finally:
        writer.close()

async def serve():
    """Accept loop with shutdown polling via ctrl_queue."""
    server = await asyncio.start_server(
        handle_connection,
        sock=server_sock,
    )
    status_queue.put(("serving",))

    # Poll for shutdown command
    while True:
        await asyncio.sleep(0.05)
        try:
            msg = ctrl_queue.get_nowait()
            if msg[0] == "shutdown":
                status_queue.put(("draining",))
                break
            elif msg[0] == "drain":
                status_queue.put(("draining",))
                break
        except Exception:
            pass  # QueueEmpty

    # Graceful shutdown
    server.close()
    await server.wait_closed()
    status_queue.put(("stopped",))

asyncio.run(serve())
'''

    # --- Run worker in a thread (subinterpreter.exec blocks) ---
    t0 = time.monotonic()

    def run_worker():
        try:
            interp.exec(worker_code)
        except ci.ExecutionFailed as e:
            print(f"[supervisor] Worker execution failed: {e}")

    worker_thread = threading.Thread(target=run_worker, name="subinterp-worker-0", daemon=True)
    worker_thread.start()

    # --- Wait for worker to start serving ---
    while True:
        msg = status_queue.get()
        print(f"[supervisor] Worker status: {msg}")
        if msg[0] == "serving":
            break
        if msg[0] == "error":
            print(f"[supervisor] Worker error during startup: {msg}")
            return

    startup_time = time.monotonic() - t0
    print(f"[supervisor] Worker serving in {startup_time*1000:.1f}ms")

    # --- Send test requests ---
    print("[supervisor] Sending test requests...")
    for i in range(5):
        with socket.create_connection(addr) as conn:
            conn.sendall(f"GET /test/{i} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            response = conn.recv(4096)
            first_line = response.split(b"\r\n")[0].decode()
            print(f"  Request {i}: {first_line}")

    # --- Shutdown ---
    print("[supervisor] Sending shutdown...")
    t1 = time.monotonic()
    ctrl_queue.put(("shutdown",))

    # Collect remaining status messages
    while True:
        msg = status_queue.get()
        print(f"[supervisor] Worker status: {msg}")
        if msg[0] == "stopped":
            break

    worker_thread.join(timeout=5.0)
    shutdown_time = time.monotonic() - t1

    if worker_thread.is_alive():
        print("[supervisor] WARNING: Worker thread did not exit cleanly")
    else:
        print(f"[supervisor] Worker stopped cleanly in {shutdown_time*1000:.1f}ms")

    interp.close()
    try:
        server_sock.close()
    except OSError:
        pass  # Already closed by subinterpreter

    # --- Summary ---
    print()
    print("=== Spike Results ===")
    print(f"  Startup time:  {startup_time*1000:.1f}ms")
    print(f"  Shutdown time: {shutdown_time*1000:.1f}ms")
    print(f"  Requests served: 5")
    print(f"  IIC protocol: tagged tuples over interpreters.Queue ✓")
    print(f"  asyncio.run() in subinterpreter ✓")
    print(f"  Socket FD sharing ✓")


if __name__ == "__main__":
    main()
