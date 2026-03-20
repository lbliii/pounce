"""
Command-line interface for pounce.

Provides the ``pounce`` command::

    pounce myapp:app --host 0.0.0.0 --port 8000 --workers 4

Uses argparse (stdlib) — no extra dependencies.

"""

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

    # Ensure current directory is on sys.path so that local modules
    # can be imported (e.g., ``pounce myapp:app`` from the project dir).
    if "" not in sys.path and "." not in sys.path:
        sys.path.insert(0, ".")

    # Resolve the ASGI app
    try:
        app = import_app(parsed.app)
    except (ValueError, ImportError, AttributeError, TypeError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)

    reload_include = parse_extensions(parsed.reload_include)
    reload_dirs = parse_dirs(parsed.reload_dir)

    # Build config from CLI arguments
    config = ServerConfig(
        host=parsed.host,
        port=parsed.port,
        workers=parsed.workers,
        log_level=parsed.log_level,
        log_format=parsed.log_format,
        root_path=parsed.root_path,
        compression=not parsed.no_compression,
        server_timing=parsed.server_timing,
        access_log=not parsed.no_access_log,
        ssl_certfile=parsed.ssl_certfile,
        ssl_keyfile=parsed.ssl_keyfile,
        http3_enabled=parsed.http3,
        reload=parsed.reload,
        reload_include=reload_include,
        reload_dirs=reload_dirs,
        keep_alive_timeout=parsed.keep_alive_timeout,
        header_timeout=parsed.header_timeout,
        max_requests_per_connection=parsed.max_requests_per_connection,
        shutdown_timeout=parsed.shutdown_timeout,
        uds=parsed.uds,
        health_check_path=parsed.health_check_path,
        worker_mode=parsed.worker_mode,
        cpu_affinity=parsed.cpu_affinity,
    )

    # Run the server — pass the original import string so that the reload
    # loop can reimport a fresh app after code changes on disk.
    server = Server(config, app, app_path=parsed.app)
    server.run()


def parse_extensions(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated extensions string into a normalized tuple.

    Ensures each extension starts with a dot and strips whitespace.
    Empty entries are filtered out.

    Args:
        raw: Comma-separated string (e.g. ``".html,.css,md"``), or None.

    Returns:
        Tuple of normalized extensions (e.g. ``(".html", ".css", ".md")``).

    """
    if not raw:
        return ()
    return tuple(
        ext.strip() if ext.strip().startswith(".") else f".{ext.strip()}"
        for ext in raw.split(",")
        if ext.strip()
    )


def parse_dirs(raw: list[str] | None) -> tuple[str, ...]:
    """Parse a list of directory strings into a cleaned tuple.

    Strips whitespace and filters empty entries.

    Args:
        raw: List of directory paths (from argparse ``append``), or None.

    Returns:
        Tuple of cleaned directory paths.

    """
    if not raw:
        return ()
    return tuple(d.strip() for d in raw if d.strip())


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

    from pounce import __version__

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
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
        "--uds",
        default=None,
        help="Unix domain socket path (e.g., /run/pounce.sock). Mutually exclusive with --host/--port.",
    )
    parser.add_argument(
        "--health-check-path",
        default=None,
        help="Path for built-in health check endpoint (e.g., /health). Disabled by default.",
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
        "--worker-mode",
        default="auto",
        choices=["auto", "sync", "async"],
        help=(
            "Worker execution model (default: auto). "
            "auto: sync on 3.14t, async on GIL. sync: blocking I/O (request-response only). "
            "async: event loop (streaming, WebSocket)."
        ),
    )
    parser.add_argument(
        "--cpu-affinity",
        action="store_true",
        default=False,
        help="Pin each worker to a CPU core (Linux only). Reduces cache thrashing.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Log level (default: info)",
    )
    parser.add_argument(
        "--log-format",
        default="auto",
        choices=["auto", "text", "json"],
        help="Log output format: auto (pretty on TTY, JSON when piped), text, or json (default: auto)",
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
    parser.add_argument(
        "--http3",
        action="store_true",
        default=False,
        help="Enable HTTP/3 (QUIC/UDP). Requires TLS (--ssl-certfile, --ssl-keyfile).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload on source file changes (development mode)",
    )
    parser.add_argument(
        "--reload-include",
        default=None,
        help=(
            "Extra file extensions to watch when --reload is active "
            '(comma-separated, e.g. ".html,.css,.md")'
        ),
    )
    parser.add_argument(
        "--reload-dir",
        action="append",
        default=None,
        help=(
            "Extra directory to watch when --reload is active "
            "(can be repeated, e.g. --reload-dir ./templates --reload-dir ./static)"
        ),
    )
    parser.add_argument(
        "--keep-alive-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for a new request on an idle keep-alive connection (default: 5.0)",
    )
    parser.add_argument(
        "--header-timeout",
        type=float,
        default=10.0,
        help="Seconds to receive complete request headers before closing (slowloris protection, default: 10.0)",
    )
    parser.add_argument(
        "--max-requests-per-connection",
        type=int,
        default=0,
        help="Max requests per keep-alive connection; 0 = unlimited (default: 0)",
    )
    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        default=10.0,
        help=(
            "Max seconds per worker (and per accept/async helper thread) during shutdown; "
            "worker joins run in parallel (default: 10). "
            "Lower values (e.g. 2) exit faster during development."
        ),
    )

    return parser
