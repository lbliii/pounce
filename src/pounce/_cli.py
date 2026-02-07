"""
Command-line interface for pounce.

Provides the ``pounce`` command::

    pounce myapp:app --host 0.0.0.0 --port 8000 --workers 4

Uses argparse (stdlib) — no extra dependencies.

"""

from __future__ import annotations

import argparse
import sys

from pounce._importer import import_app
from pounce.config import ServerConfig
from pounce.server import Server


def main(args: list[str] | None = None) -> None:
    """Entry point for the ``pounce`` CLI command.

    Args:
        args: Command-line arguments (defaults to sys.argv[1:]).

    """
    parser = _build_parser()
    parsed = parser.parse_args(args)

    # Resolve the ASGI app
    try:
        app = import_app(parsed.app)
    except (ValueError, ImportError, AttributeError, TypeError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)

    # Build config from CLI arguments
    config = ServerConfig(
        host=parsed.host,
        port=parsed.port,
        workers=parsed.workers,
        log_level=parsed.log_level,
        root_path=parsed.root_path,
        compression=not parsed.no_compression,
        server_timing=parsed.server_timing,
        access_log=not parsed.no_access_log,
        ssl_certfile=parsed.ssl_certfile,
        ssl_keyfile=parsed.ssl_keyfile,
    )

    # Run the server
    server = Server(config, app)
    server.run()


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the pounce CLI."""
    parser = argparse.ArgumentParser(
        prog="pounce",
        description="Pounce — A free-threading-native ASGI server for Python 3.14t",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  pounce myapp:app\n"
            "  pounce myapp:app --host 0.0.0.0 --port 8000\n"
            "  pounce myapp:app --workers 4 --log-level debug\n"
            "  pounce myapp.web:create_app() --root-path /api\n"
        ),
    )

    parser.add_argument(
        "app",
        help="ASGI application (e.g., 'myapp:app' or 'myapp:create_app()')",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of workers (default: 1). "
            "Use 0 for auto-detect (one per CPU core). "
            "On nogil builds workers are threads; on GIL builds they are processes."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Log level (default: info)",
    )
    parser.add_argument(
        "--root-path",
        default="",
        help="ASGI root_path for reverse proxy setups",
    )
    parser.add_argument(
        "--no-compression",
        action="store_true",
        default=False,
        help="Disable response compression",
    )
    parser.add_argument(
        "--server-timing",
        action="store_true",
        default=False,
        help="Enable Server-Timing header injection",
    )
    parser.add_argument(
        "--no-access-log",
        action="store_true",
        default=False,
        help="Disable access logging",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=None,
        help="Path to TLS certificate file (enables HTTPS)",
    )
    parser.add_argument(
        "--ssl-keyfile",
        default=None,
        help="Path to TLS private key file",
    )

    return parser
