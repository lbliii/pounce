"""
Spike: Import a real ASGI app by module path inside a subinterpreter.

Tests:
1. Import pounce config inside subinterpreter
2. Reconstruct frozen ServerConfig from dict
3. Import an example ASGI app
4. Test config serialization round-trip via IIC-safe types
"""

import concurrent.interpreters as ci
import json
import sys
import time


def main() -> None:
    status_queue = ci.create_queue()
    interp = ci.create()

    # Pass sys.path as tuple (IIC-safe) so subinterpreter can find modules.
    # We must resolve '' to CWD and also add the project root explicitly,
    # because editable install path hooks don't transfer to subinterpreters.
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    resolved = [os.path.abspath(p) if p == "" else p for p in sys.path]
    # Add project root so packages like 'examples' are importable
    if project_root not in resolved:
        resolved.insert(0, project_root)
    # Filter out path hooks (not transferable to subinterpreters)
    sys_path = tuple(p for p in resolved if not p.startswith("__editable__"))

    # Serialize config as JSON string (str is IIC-safe)
    config_json = json.dumps({
        "host": "127.0.0.1",
        "port": 8000,
        "workers": 4,
        "keep_alive_timeout": 5.0,
        "request_timeout": 30.0,
        "compression": True,
        "debug": False,
    })

    interp.prepare_main(
        status_queue=status_queue,
        config_json=config_json,
        app_import_path="examples.hello:app",
        parent_sys_path=sys_path,
    )

    worker_code = r'''
import importlib
import json
import sys
import time

t0 = time.monotonic()

# --- Inherit sys.path from parent ---
sys.path[:] = list(parent_sys_path)
status_queue.put(("ok", f"sys.path set: {len(sys.path)} entries, first={sys.path[0]}"))

# --- Test 1: Import pounce.config ---
try:
    from pounce.config import ServerConfig
    status_queue.put(("ok", "pounce.config imported"))
except Exception as e:
    status_queue.put(("error", f"pounce.config import failed: {e}"))

# --- Test 2: Reconstruct config from dict ---
try:
    config_dict = json.loads(config_json)
    config = ServerConfig(**config_dict)
    status_queue.put(("ok", f"ServerConfig created: host={config.host}, workers={config.workers}"))
except Exception as e:
    status_queue.put(("error", f"ServerConfig creation failed: {e}"))

# --- Test 3: Import ASGI app by path ---
try:
    module_path, _, attr = app_import_path.rpartition(":")
    # Handle dotted module paths like "examples.hello"
    mod = importlib.import_module(module_path)
    app = getattr(mod, attr)
    status_queue.put(("ok", f"App imported: {app}"))
except Exception as e:
    status_queue.put(("error", f"App import failed: {e}"))

# --- Test 4: asyncio.run works ---
try:
    import asyncio

    async def test_app():
        scope = {"type": "http", "method": "GET", "path": "/"}
        response_parts = []
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(msg):
            response_parts.append(msg)
        await app(scope, receive, send)
        return response_parts

    parts = asyncio.run(test_app())
    body = parts[1]["body"] if len(parts) > 1 else b"(no body)"
    status_queue.put(("ok", f"App response: {body[:100]}"))
except Exception as e:
    status_queue.put(("error", f"asyncio.run failed: {e}"))

elapsed = time.monotonic() - t0
status_queue.put(("done", f"{elapsed*1000:.1f}ms"))
'''

    t0 = time.monotonic()
    interp.exec(worker_code)
    wall_time = time.monotonic() - t0

    # Collect results
    results = []
    while True:
        try:
            msg = status_queue.get_nowait()
            results.append(msg)
            if msg[0] == "done":
                break
        except Exception:
            break

    interp.close()

    print("=== Subinterpreter Import Spike ===")
    for status, detail in results:
        icon = "✓" if status == "ok" else ("⏱" if status == "done" else "✗")
        print(f"  {icon} {detail}")
    print(f"  Wall time: {wall_time*1000:.1f}ms")


if __name__ == "__main__":
    main()
