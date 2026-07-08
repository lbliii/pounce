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

from collections.abc import Callable  # noqa: E402
from typing import Any, TypedDict, Unpack, overload  # noqa: E402

from pounce._errors import (  # noqa: E402
    LifespanError,
    PounceError,
    ReloadError,
    SupervisorError,
    TLSError,
)
from pounce._middleware import (  # noqa: E402
    CORSMiddleware,
    Middleware,
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
    worker_mode: str
    worker_startup_failure: str
    cpu_affinity: bool
    executor_threads_per_worker: int
    keep_alive_timeout: float
    request_timeout: float
    write_timeout: float
    header_timeout: float
    startup_timeout: float
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
    app_name: str | None
    app_tagline: str | None
    app_version: str | None
    signage: str | None
    access_log_filter: Callable[[str, str, int], bool] | None
    server_header: str
    date_header: bool
    root_path: str
    compression: bool
    compression_min_size: int
    compression_dictionaries: tuple[Any, ...]
    server_timing: bool
    debug: bool
    reload: bool
    reload_include: tuple[str, ...]
    reload_dirs: tuple[str, ...]
    h11_max_incomplete_event_size: int | None
    trusted_hosts: frozenset[str]
    trusted_hosts_wildcard: bool
    forwarded_for_trusted_hops: int
    health_check_path: str | None
    introspection_enabled: bool
    introspection_bind: str
    introspection_path: str
    uds: str | None
    uds_permissions: int
    ssl_certfile: str | None
    ssl_keyfile: str | None
    static_files: dict[str, str]
    static_cache_control: str
    static_precompressed: bool
    static_follow_symlinks: bool
    static_index_file: str | None
    middleware: list[Middleware]
    websocket_compression: bool
    websocket_max_message_size: int
    reload_timeout: float
    otel_endpoint: str | None
    otel_service_name: str
    lifecycle_logging: bool
    log_slow_requests_threshold: float
    metrics_enabled: bool
    metrics_path: str
    rate_limit_enabled: bool
    rate_limit_requests_per_second: float
    rate_limit_burst: int
    rate_limit_max_tracked_ips: int
    request_queue_enabled: bool
    request_queue_max_depth: int
    http3_enabled: bool
    http3_max_connections: int
    http3_idle_timeout: float
    http3_qpack_max_table_capacity: int
    http3_zero_rtt_enabled: bool
    sentry_dsn: str | None
    sentry_environment: str | None
    sentry_release: str | None
    sentry_traces_sample_rate: float
    sentry_profiles_sample_rate: float


@overload
def run(app: str | ASGIApp, *, config: ServerConfig) -> None: ...


@overload
def run(app: str | ASGIApp, **kwargs: Unpack[ServerConfigKwargs]) -> None: ...


def run(app: str | ASGIApp, **kwargs: Any) -> None:
    """Start a pounce server.

    Args:
        app: ASGI application import string (e.g., "myapp:app") or callable.
        **kwargs: Server configuration overrides passed to ServerConfig.
            Pass ``config=ServerConfig(...)`` to use a pre-built config directly.

    Example:
        >>> import pounce
        >>> pounce.run("myapp:app", host="0.0.0.0", port=8000, workers=4)
        >>> pounce.run(app, config=ServerConfig(host="0.0.0.0", workers=4))

    """
    from pounce.server import Server

    # Accept a pre-built ServerConfig via config= kwarg.
    _missing = object()
    pre_built = kwargs.pop("config", _missing)
    if pre_built is _missing:
        config = ServerConfig(**kwargs)
    elif isinstance(pre_built, ServerConfig):
        if kwargs:
            msg = "Cannot pass both config=ServerConfig(...) and additional keyword arguments"
            raise TypeError(msg)
        config = pre_built
    else:
        msg = "config must be a ServerConfig instance"
        raise TypeError(msg)

    if isinstance(app, str):
        from pounce._importer import import_app

        server = Server(config, import_app(app), app_path=app)
    else:
        server = Server(config, app)
    server.run()


__all__ = [
    "ASGIApp",
    "CORSMiddleware",
    "LifespanError",
    "PounceError",
    "Receive",
    "Response",
    "Scope",
    "SecurityHeadersMiddleware",
    "Send",
    "ServerConfig",
    "ServerConfigKwargs",
    "StaticFiles",
    "__version__",
    "create_static_handler",
    "run",
]
