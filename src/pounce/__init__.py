"""
Pounce — A free-threading-native ASGI server for Python 3.14t.

Pounce is a pure-Python ASGI server designed from scratch for Python's free-threading
mode (PEP 703). Instead of the traditional fork-based worker model, pounce runs N worker
threads sharing a single interpreter — leveraging nogil for true parallelism without the
memory overhead of multi-process deployments.

Quick start:

    import pounce

    pounce.run("myapp:app", host="0.0.0.0", port=8000)

Or from the command line:

    pounce myapp:app --workers 4

Part of the Bengal ecosystem:

    pounce      ASGI server       (serves apps)
    chirp       Web framework     (serves HTML)
    kida        Template engine   (renders HTML)
    patitas     Markdown parser   (parses content)
    rosettes    Syntax highlighter (highlights code)
    bengal      Static site gen   (builds sites)

"""

# PEP 703: Declare this module as free-threading safe
_Py_mod_gil = 0

try:
    from importlib.metadata import version as _get_pkg_version

    __version__ = _get_pkg_version("bengal-pounce")
except Exception:  # Package not installed in editable/dist mode
    __version__ = "0.0.0-dev"

from typing import TypedDict, Unpack  # noqa: E402

from pounce._errors import (  # noqa: E402
    LifespanError,
    PounceError,
    ReloadError,
    SupervisorError,
    TLSError,
)
from pounce._middleware import (  # noqa: E402
    CORSMiddleware,
    Response,
    SecurityHeadersMiddleware,
)
from pounce._static import StaticFiles, create_static_handler  # noqa: E402
from pounce._types import ASGIApp, Receive, Scope, Send  # noqa: E402
from pounce.config import ServerConfig  # noqa: E402
from pounce.display import DisplayConfig  # noqa: E402
from pounce.lifecycle import (  # noqa: E402
    BufferedCollector,
    ClientDisconnected,
    ConnectionCompleted,
    ConnectionOpened,
    LifecycleCollector,
    LifecycleEvent,
    LoggingCollector,
    NoopCollector,
    RequestStarted,
    ResponseCompleted,
)


class ServerConfigKwargs(TypedDict, total=False):
    """Typed keyword arguments matching ``ServerConfig`` fields."""

    host: str
    port: int
    workers: int
    backlog: int
    keep_alive_timeout: float
    request_timeout: float
    header_timeout: float
    shutdown_timeout: float
    max_request_size: int
    max_header_size: int
    max_headers: int
    max_connections: int
    max_requests_per_connection: int
    access_log: bool
    log_level: str
    log_format: str
    display: DisplayConfig | None
    server_header: str
    date_header: bool
    root_path: str
    compression: bool
    compression_min_size: int
    server_timing: bool
    reload: bool
    reload_include: tuple[str, ...]
    reload_dirs: tuple[str, ...]
    h11_max_incomplete_event_size: int | None
    trusted_hosts: frozenset[str]
    trusted_hosts_wildcard: bool
    health_check_path: str | None
    uds: str | None
    ssl_certfile: str | None
    ssl_keyfile: str | None


def run(app: str | ASGIApp, **kwargs: Unpack[ServerConfigKwargs]) -> None:
    """Start a pounce server.

    Args:
        app: ASGI application import string (e.g., "myapp:app") or callable.
        **kwargs: Server configuration overrides passed to ServerConfig.

    Example:
        >>> import pounce
        >>> pounce.run("myapp:app", host="0.0.0.0", port=8000, workers=4)

    """
    from pounce.server import Server

    config = ServerConfig(**kwargs)
    if isinstance(app, str):
        from pounce._importer import import_app

        server = Server(config, import_app(app), app_path=app)
    else:
        server = Server(config, app)
    server.run()


__all__ = [
    "ASGIApp",
    "BufferedCollector",
    "CORSMiddleware",
    "ClientDisconnected",
    "ConnectionCompleted",
    "ConnectionOpened",
    "DisplayConfig",
    "LifecycleCollector",
    "LifecycleEvent",
    "LifespanError",
    "LoggingCollector",
    "NoopCollector",
    "PounceError",
    "Receive",
    "ReloadError",
    "RequestStarted",
    "Response",
    "ResponseCompleted",
    "Scope",
    "SecurityHeadersMiddleware",
    "Send",
    "ServerConfig",
    "StaticFiles",
    "SupervisorError",
    "TLSError",
    "__version__",
    "create_static_handler",
    "run",
]
