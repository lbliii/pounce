"""
Server configuration.

ServerConfig is the single configuration object for a pounce server instance.
Frozen after creation — the server reads config but never mutates it.

"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Immutable server configuration.

    All settings for a pounce server instance. Created once at startup,
    shared across all worker threads (safe because frozen).

    Example:
        config = ServerConfig(host="0.0.0.0", port=8000, workers=4)

    """

    # Bind address
    host: str = "127.0.0.1"
    port: int = 8000

    # Worker configuration
    # 0 = auto-detect from os.cpu_count(), 1 = single-worker (no supervisor),
    # 2+ = explicit multi-worker with supervisor
    workers: int = 1
    backlog: int = 2048

    # Worker execution model (multi-worker only)
    # "auto": sync on 3.14t, async on GIL (default)
    # "sync": force sync workers (fast path; streaming hands off to async pool)
    # "async": force async workers (current behavior)
    worker_mode: str = "auto"

    # Per-worker thread pool for asyncio.to_thread() calls.
    # In thread mode (3.14t), all workers share one process and the default
    # ThreadPoolExecutor — causing contention under high concurrency.
    # Setting this > 0 gives each worker its own executor.
    # 0 = auto-size (min(32, cpu_count + 4) per worker).
    executor_threads_per_worker: int = 0

    # Timeouts (seconds)
    keep_alive_timeout: float = 5.0
    request_timeout: float = 30.0
    header_timeout: float = 10.0
    startup_timeout: float = 30.0
    shutdown_timeout: float = 10.0

    # Limits
    max_request_size: int = 1_048_576  # 1 MB
    max_header_size: int = 65_536  # 64 KB
    max_headers: int = 100
    max_connections: int = 10_000
    max_requests_per_connection: int = 0  # 0 = unlimited

    # Logging
    access_log: bool = True
    log_level: str = "info"
    log_format: str = "text"  # "text" or "json"
    # Optional filter: (method, path, status) -> bool.  True = log, False = skip.
    access_log_filter: Callable[[str, str, int], bool] | None = None

    # HTTP
    server_header: str = "pounce"
    date_header: bool = True
    root_path: str = ""

    # Content encoding — negotiated per-request via Accept-Encoding
    # zstd uses stdlib compression.zstd (PEP 784), gzip uses stdlib zlib
    compression: bool = True
    compression_min_size: int = 500  # Don't compress responses smaller than this (bytes)

    # Server-Timing header — auto-injected with parse/app/encode durations
    server_timing: bool = False

    # Development
    debug: bool = False  # Enable rich error pages (never use in production!)
    reload: bool = False
    reload_include: tuple[str, ...] = ()  # Extra file extensions to watch (e.g. ".html", ".css")
    reload_dirs: tuple[str, ...] = ()  # Extra directories to watch alongside cwd

    # h11 tuning
    h11_max_incomplete_event_size: int | None = None  # None = h11 default (16 KB)

    # Headers to trust from proxy (empty = direct connection)
    trusted_hosts: tuple[str, ...] = field(default_factory=tuple)

    # Built-in health check endpoint (None = disabled)
    health_check_path: str | None = None

    # Unix domain socket (mutually exclusive with host/port)
    uds: str | None = None

    # TLS (optional — phase 3)
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None

    # Static file serving (phase 5b)
    static_files: dict[str, str] = field(default_factory=dict)  # {url_path: directory}
    static_cache_control: str = "public, max-age=3600"
    static_precompressed: bool = True
    static_follow_symlinks: bool = False
    static_index_file: str | None = "index.html"

    # Middleware (phase 5b)
    middleware: list[Callable[..., Any]] = field(default_factory=list)

    # WebSocket (phase 5b)
    websocket_compression: bool = True  # Enable permessage-deflate compression
    websocket_max_message_size: int = 10_485_760  # 10 MB

    # Graceful reload (phase 5b)
    reload_timeout: float = 30.0  # Time to wait for workers to drain during reload

    # OpenTelemetry (phase 5b)
    otel_endpoint: str | None = None  # OTLP endpoint (e.g., "http://localhost:4318")
    otel_service_name: str = "pounce"  # Service name in traces

    # Structured lifecycle event logging (phase 5b)
    lifecycle_logging: bool = False  # Enable structured lifecycle event logging
    log_slow_requests_threshold: float = 5.0  # Log requests slower than this (seconds)

    # Prometheus metrics endpoint (phase 6)
    metrics_enabled: bool = False  # Enable Prometheus /metrics endpoint
    metrics_path: str = "/metrics"  # Path for metrics endpoint

    # Rate limiting (phase 6.2)
    rate_limit_enabled: bool = False  # Enable per-IP rate limiting
    rate_limit_requests_per_second: float = 100.0  # Requests per second per IP
    rate_limit_burst: int = 200  # Maximum burst size per IP

    # Request queuing and load shedding (phase 6.3)
    request_queue_enabled: bool = False  # Enable request queueing
    request_queue_max_depth: int = 1000  # Maximum queued requests (0 = unlimited)

    # HTTP/3 (phase 5c) — QUIC/UDP, requires TLS
    http3_enabled: bool = False  # Enable HTTP/3 (requires ssl_certfile, ssl_keyfile)
    http3_max_connections: int = 10_000  # Max concurrent QUIC connections
    http3_idle_timeout: float = 30.0  # QUIC idle timeout (seconds)

    # Sentry error tracking (phase 6.4)
    sentry_dsn: str | None = None  # Sentry DSN for error tracking (None = disabled)
    sentry_environment: str | None = None  # Environment name (e.g., "production")
    sentry_release: str | None = None  # Release version (e.g., "myapp@1.0.0")
    sentry_traces_sample_rate: float = 0.1  # Performance monitoring sample rate (0.0-1.0)
    sentry_profiles_sample_rate: float = 0.1  # Profiling sample rate (0.0-1.0)

    _VALID_LOG_LEVELS: frozenset[str] = frozenset({"debug", "info", "warning", "error", "critical"})
    _VALID_LOG_FORMATS: frozenset[str] = frozenset({"text", "json"})
    _VALID_WORKER_MODES: frozenset[str] = frozenset({"auto", "sync", "async"})

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if not self.host:
            msg = "host must be a non-empty string"
            raise ValueError(msg)
        if self.port < 0 or self.port > 65535:
            msg = f"port must be 0-65535 (got {self.port})"
            raise ValueError(msg)
        if self.workers < 0:
            msg = f"workers must be >= 0 (got {self.workers})"
            raise ValueError(msg)
        if self.backlog <= 0:
            msg = f"backlog must be > 0 (got {self.backlog})"
            raise ValueError(msg)
        if self.executor_threads_per_worker < 0:
            msg = (
                f"executor_threads_per_worker must be >= 0 "
                f"(got {self.executor_threads_per_worker})"
            )
            raise ValueError(msg)
        if self.keep_alive_timeout <= 0:
            msg = f"keep_alive_timeout must be > 0 (got {self.keep_alive_timeout})"
            raise ValueError(msg)
        if self.request_timeout <= 0:
            msg = f"request_timeout must be > 0 (got {self.request_timeout})"
            raise ValueError(msg)
        if self.header_timeout <= 0:
            msg = f"header_timeout must be > 0 (got {self.header_timeout})"
            raise ValueError(msg)
        if self.shutdown_timeout <= 0:
            msg = f"shutdown_timeout must be > 0 (got {self.shutdown_timeout})"
            raise ValueError(msg)
        if self.max_request_size <= 0:
            msg = f"max_request_size must be > 0 (got {self.max_request_size})"
            raise ValueError(msg)
        if self.max_header_size <= 0:
            msg = f"max_header_size must be > 0 (got {self.max_header_size})"
            raise ValueError(msg)
        if self.max_headers <= 0:
            msg = f"max_headers must be > 0 (got {self.max_headers})"
            raise ValueError(msg)
        if self.max_connections < 0:
            msg = f"max_connections must be >= 0 (got {self.max_connections})"
            raise ValueError(msg)
        if self.max_requests_per_connection < 0:
            msg = (
                f"max_requests_per_connection must be >= 0 (got {self.max_requests_per_connection})"
            )
            raise ValueError(msg)
        if self.compression_min_size < 0:
            msg = f"compression_min_size must be >= 0 (got {self.compression_min_size})"
            raise ValueError(msg)
        if self.log_level.lower() not in self._VALID_LOG_LEVELS:
            msg = (
                f"log_level must be one of {sorted(self._VALID_LOG_LEVELS)} "
                f"(got {self.log_level!r})"
            )
            raise ValueError(msg)
        if self.log_format.lower() not in self._VALID_LOG_FORMATS:
            msg = (
                f"log_format must be one of {sorted(self._VALID_LOG_FORMATS)} "
                f"(got {self.log_format!r})"
            )
            raise ValueError(msg)
        if self.worker_mode.lower() not in self._VALID_WORKER_MODES:
            msg = (
                f"worker_mode must be one of {sorted(self._VALID_WORKER_MODES)} "
                f"(got {self.worker_mode!r})"
            )
            raise ValueError(msg)
        if (self.ssl_certfile is None) != (self.ssl_keyfile is None):
            msg = "ssl_certfile and ssl_keyfile must both be set or both be None"
            raise ValueError(msg)
        if self.http3_enabled:
            if self.ssl_certfile is None or self.ssl_keyfile is None:
                msg = "http3_enabled requires ssl_certfile and ssl_keyfile (QUIC mandates TLS 1.3)"
                raise ValueError(msg)
            if self.uds is not None:
                msg = "http3_enabled is not supported with Unix domain sockets"
                raise ValueError(msg)
        if self.http3_max_connections <= 0:
            msg = f"http3_max_connections must be > 0 (got {self.http3_max_connections})"
            raise ValueError(msg)
        if self.http3_idle_timeout <= 0:
            msg = f"http3_idle_timeout must be > 0 (got {self.http3_idle_timeout})"
            raise ValueError(msg)
        if self.uds is not None and not self.uds:
            msg = "uds must be a non-empty path or None"
            raise ValueError(msg)
        if self.log_slow_requests_threshold <= 0:
            msg = (
                f"log_slow_requests_threshold must be > 0 (got {self.log_slow_requests_threshold})"
            )
            raise ValueError(msg)
        if self.metrics_path and not self.metrics_path.startswith("/"):
            msg = f"metrics_path must start with / (got {self.metrics_path!r})"
            raise ValueError(msg)
        if self.rate_limit_requests_per_second <= 0:
            msg = (
                f"rate_limit_requests_per_second must be > 0 "
                f"(got {self.rate_limit_requests_per_second})"
            )
            raise ValueError(msg)
        if self.rate_limit_burst <= 0:
            msg = f"rate_limit_burst must be > 0 (got {self.rate_limit_burst})"
            raise ValueError(msg)
        if self.request_queue_max_depth < 0:
            msg = f"request_queue_max_depth must be >= 0 (got {self.request_queue_max_depth})"
            raise ValueError(msg)
        if not 0.0 <= self.sentry_traces_sample_rate <= 1.0:
            msg = (
                f"sentry_traces_sample_rate must be 0.0-1.0 (got {self.sentry_traces_sample_rate})"
            )
            raise ValueError(msg)
        if not 0.0 <= self.sentry_profiles_sample_rate <= 1.0:
            msg = (
                f"sentry_profiles_sample_rate must be 0.0-1.0 "
                f"(got {self.sentry_profiles_sample_rate})"
            )
            raise ValueError(msg)

    def resolve_workers(self) -> int:
        """Return the effective worker count.

        If ``workers`` is 0 (auto-detect), returns ``os.cpu_count()``
        (minimum 1).  Otherwise returns the explicit value.

        """
        if self.workers == 0:
            from pounce._runtime import default_worker_count

            return default_worker_count()
        return self.workers
