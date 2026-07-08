"""
Server configuration.

ServerConfig is the single configuration object for a pounce server instance.
Frozen after creation — the server reads config but never mutates it.

"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pounce._middleware import Middleware
from pounce.display import DisplayConfig

# Fields excluded from IIC serialization (non-JSON-safe or internal constants)
_IIC_SKIP_FIELDS: frozenset[str] = frozenset(
    {
        "access_log_filter",
        "compression_dictionaries",
        "middleware",
        "display",
        "app_name",
        "app_tagline",
        "app_version",
        "signage",
        "_VALID_LOG_LEVELS",
        "_VALID_LOG_FORMATS",
        "_VALID_WORKER_MODES",
        "_VALID_WORKER_STARTUP_FAILURE",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerConfig:
    """Immutable server configuration.

    All settings for a pounce server instance. Created once at startup,
    shared across all worker threads (safe because frozen).

    Example:
        config = ServerConfig(host="0.0.0.0", port=8000, workers=4)

    Stability tiers:
        Not every knob carries the same maturity. Treat the following as
        guidance for production use; ``pounce config schema`` stamps the beta
        tier as an ``x-stability`` annotation per field.

        Stable -- safe for production, behavior is covered by the core
        contract: ``host``/``port``/``uds``, ``workers``/``backlog``,
        ``worker_mode`` values ``auto``/``sync``/``async``, the timeouts
        (``*_timeout``), the limits (``max_*``), and logging
        (``access_log``/``log_level``/``log_format``).

        Beta -- usable but the behavior, surface, or proof is still firming up;
        pin versions and validate in staging before relying on them:
        ``worker_mode="subinterpreter"`` (PEP 734, limited lifecycle proof),
        the rate-limit knobs (``rate_limit_*``), the request-queue knobs
        (``request_queue_*``), introspection (``introspection_*``), HTTP/3
        (``http3_*``), and the observability integrations
        (``otel_*``/``sentry_*``/``metrics_*``).

        See ``docs/design/core-contract.md`` for the per-feature contract and
        required proof.

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
    # "subinterpreter": BETA (PEP 734) -- one subinterpreter per worker thread.
    #   Limited lifecycle proof; pin deps and validate in staging. See
    #   docs/design/subinterpreter-workers.md and core-contract.md.
    worker_mode: str = "auto"

    # Worker startup hook (pounce.worker.startup) failure policy.
    # "ignore" (default): a hook exception/timeout is logged and serving
    #   continues — required for generic ASGI apps that don't recognise the
    #   scope and raise on it (e.g. KeyError on scope['method']).
    # "shutdown": treat a hook exception/timeout as fatal — the worker refuses
    #   to serve and the server exits non-zero before reporting readiness. Lets
    #   frameworks that intentionally use the hook fail loudly instead of
    #   serving with uninitialised worker state. See issues #65 and #245.
    worker_startup_failure: str = "ignore"

    # CPU affinity (Linux only): pin each worker to a dedicated core.
    # Reduces cache thrashing; no-op on non-Linux or when sched_setaffinity fails.
    cpu_affinity: bool = False

    # Per-worker thread pool for asyncio.to_thread() calls.
    # In thread mode (3.14t), all workers share one process and the default
    # ThreadPoolExecutor — causing contention under high concurrency.
    # Setting this > 0 gives each worker its own executor.
    # 0 = auto-size (min(32, cpu_count + 4) per worker).
    executor_threads_per_worker: int = 0

    # Timeouts (seconds)
    keep_alive_timeout: float = 5.0
    request_timeout: float = 30.0
    write_timeout: float = 30.0
    header_timeout: float = 10.0
    startup_timeout: float = 30.0
    # Graceful shutdown: max seconds per worker join (parallel in multi-worker),
    # and per auxiliary thread (AcceptDistributor, AsyncPool). Not a single shared
    # deadline split across all workers.
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
    log_format: str = "auto"  # "auto", "text", or "json"
    # Optional application branding for startup banner / JSON startup line
    display: DisplayConfig | None = None
    # Branding fields settable via config file (merged into DisplayConfig at startup)
    app_name: str | None = None
    app_tagline: str | None = None
    app_version: str | None = None
    signage: str | None = None
    # Optional filter: (method, path, status) -> bool.  True = log, False = skip.
    access_log_filter: Callable[[str, str, int], bool] | None = None

    # HTTP
    http2_enabled: bool = True  # Advertise h2 via TLS ALPN when the extra is installed
    server_header: str = "pounce"
    date_header: bool = True
    root_path: str = ""

    # Content encoding — negotiated per-request via Accept-Encoding
    # zstd uses stdlib compression.zstd (PEP 784), gzip uses stdlib zlib
    compression: bool = True
    compression_min_size: int = 500  # Don't compress responses smaller than this (bytes)
    # Dictionary compression (RFC 9842) — loaded zstd dictionaries for
    # dramatically better compression on repetitive payloads (e.g. API JSON)
    compression_dictionaries: tuple[Any, ...] = ()

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
    trusted_hosts: frozenset[str] = field(default_factory=frozenset)
    trusted_hosts_wildcard: bool = False
    # Number of trusted reverse-proxy hops in front of pounce. The real client
    # IP is taken this many positions from the RIGHT of X-Forwarded-For, so a
    # client-supplied (leftmost) value cannot spoof the perceived client IP.
    # Default 1 matches a single trusted proxy. See issue #108.
    forwarded_for_trusted_hops: int = 1

    # Built-in health check endpoint (None = disabled)
    health_check_path: str | None = None

    # Built-in /_pounce/info introspection endpoint (Sprint 5).
    # Disabled by default; loopback-bound; allowlist-driven exposure via
    # INFO_ALLOWLIST in _config_schema.py. See docs/troubleshooting.md
    # #POUNCE_CONFIG_INTROSPECTION_PUBLIC for the public-bind warning.
    introspection_enabled: bool = False
    introspection_bind: str = "127.0.0.1"
    introspection_path: str = "/_pounce/info"

    # Unix domain socket (mutually exclusive with host/port)
    uds: str | None = None
    uds_permissions: int = 0o660  # File mode for UDS socket (default: owner+group rw)

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
    middleware: list[Middleware] = field(default_factory=list)

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
    # PER-WORKER: each worker holds an independent token bucket in process/
    # subinterpreter modes (fork copies, no shared IPC), so the real per-IP
    # ceiling is rate x workers and burst x workers. In single-worker
    # mode the configured value IS the aggregate. See issue #109 and
    # docs/deployment/backpressure.md.
    rate_limit_enabled: bool = False  # Enable per-IP rate limiting (per worker)
    rate_limit_requests_per_second: float = 100.0  # Requests/sec per IP, per worker
    rate_limit_burst: int = 200  # Maximum burst size per IP, per worker
    # Hard cap on distinct client IPs tracked by the limiter. Bounds memory
    # under a unique-IP flood (LRU eviction when exceeded). See issue #110.
    rate_limit_max_tracked_ips: int = 100_000

    # Request queuing and load shedding (phase 6.3)
    # PER-WORKER in ALL modes (thread, process, subinterpreter): the queue uses
    # an asyncio.Semaphore bound to one event loop, so every worker gets its own
    # queue. The effective load-shed depth is max_depth x workers always. See
    # issue #109 and docs/deployment/backpressure.md.
    request_queue_enabled: bool = False  # Enable request queueing (per worker)
    request_queue_max_depth: int = 1000  # Max queued requests per worker (0 = unlimited)

    # HTTP/3 (phase 5c) — QUIC/UDP, requires TLS
    http3_enabled: bool = False  # Enable HTTP/3 (requires ssl_certfile, ssl_keyfile)
    http3_max_connections: int = 10_000  # Max concurrent QUIC connections
    http3_idle_timeout: float = 30.0  # QUIC idle timeout (seconds)
    http3_qpack_max_table_capacity: int = 0  # QPACK dynamic table size (0 = static-only)
    http3_zero_rtt_enabled: bool = False  # Accept 0-RTT early data (safe methods only)

    # Sentry error tracking (phase 6.4)
    sentry_dsn: str | None = None  # Sentry DSN for error tracking (None = disabled)
    sentry_environment: str | None = None  # Environment name (e.g., "production")
    sentry_release: str | None = None  # Release version (e.g., "myapp@1.0.0")
    sentry_traces_sample_rate: float = 0.1  # Performance monitoring sample rate (0.0-1.0)
    sentry_profiles_sample_rate: float = 0.1  # Profiling sample rate (0.0-1.0)

    _VALID_LOG_LEVELS: frozenset[str] = frozenset({"debug", "info", "warning", "error", "critical"})
    _VALID_LOG_FORMATS: frozenset[str] = frozenset({"auto", "text", "json"})
    _VALID_WORKER_MODES: frozenset[str] = frozenset({"auto", "sync", "async", "subinterpreter"})
    _VALID_WORKER_STARTUP_FAILURE: frozenset[str] = frozenset({"ignore", "shutdown"})

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
                f"executor_threads_per_worker must be >= 0 (got {self.executor_threads_per_worker})"
            )
            raise ValueError(msg)
        if self.keep_alive_timeout <= 0:
            msg = f"keep_alive_timeout must be > 0 (got {self.keep_alive_timeout})"
            raise ValueError(msg)
        if self.request_timeout <= 0:
            msg = f"request_timeout must be > 0 (got {self.request_timeout})"
            raise ValueError(msg)
        if self.write_timeout <= 0:
            msg = f"write_timeout must be > 0 (got {self.write_timeout})"
            raise ValueError(msg)
        if self.header_timeout <= 0:
            msg = f"header_timeout must be > 0 (got {self.header_timeout})"
            raise ValueError(msg)
        if self.startup_timeout <= 0:
            msg = f"startup_timeout must be > 0 (got {self.startup_timeout})"
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
        normalized = self.worker_mode.lower()
        if normalized not in self._VALID_WORKER_MODES:
            msg = (
                f"worker_mode must be one of {sorted(self._VALID_WORKER_MODES)} "
                f"(got {self.worker_mode!r})"
            )
            raise ValueError(msg)
        object.__setattr__(self, "worker_mode", normalized)
        wsf = self.worker_startup_failure.lower()
        if wsf not in self._VALID_WORKER_STARTUP_FAILURE:
            msg = (
                f"worker_startup_failure must be one of "
                f"{sorted(self._VALID_WORKER_STARTUP_FAILURE)} "
                f"(got {self.worker_startup_failure!r})"
            )
            raise ValueError(msg)
        object.__setattr__(self, "worker_startup_failure", wsf)
        if normalized == "subinterpreter":
            from pounce._runtime import has_subinterpreters

            if not has_subinterpreters():
                msg = (
                    "worker_mode='subinterpreter' requires Python 3.14+ with "
                    "concurrent.interpreters (PEP 734)"
                )
                raise ValueError(msg)
        # Normalize trusted_hosts to frozenset if passed as tuple/list
        if not isinstance(self.trusted_hosts, frozenset):
            object.__setattr__(self, "trusted_hosts", frozenset(self.trusted_hosts))
        object.__setattr__(self, "trusted_hosts_wildcard", "*" in self.trusted_hosts)
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
        if self.http3_qpack_max_table_capacity < 0:
            msg = f"http3_qpack_max_table_capacity must be >= 0 (got {self.http3_qpack_max_table_capacity})"
            raise ValueError(msg)
        if self.uds is not None and not self.uds:
            msg = "uds must be a non-empty path or None"
            raise ValueError(msg)
        if self.signage is not None:
            from pounce.display import _VALID_SIGNAGE

            if self.signage.strip().lower() not in _VALID_SIGNAGE:
                msg = f"signage must be one of {sorted(_VALID_SIGNAGE)} (got {self.signage!r})"
                raise ValueError(msg)
        if self.log_slow_requests_threshold <= 0:
            msg = (
                f"log_slow_requests_threshold must be > 0 (got {self.log_slow_requests_threshold})"
            )
            raise ValueError(msg)
        if self.metrics_path and not self.metrics_path.startswith("/"):
            msg = f"metrics_path must start with / (got {self.metrics_path!r})"
            raise ValueError(msg)
        if self.health_check_path and not self.health_check_path.startswith("/"):
            msg = f"health_check_path must start with / (got {self.health_check_path!r})"
            raise ValueError(msg)
        if not self.introspection_path.startswith("/"):
            msg = f"introspection_path must start with / (got {self.introspection_path!r})"
            raise ValueError(msg)
        if not self.introspection_bind:
            msg = "introspection_bind must be a non-empty string"
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
        if self.rate_limit_max_tracked_ips < 1:
            msg = f"rate_limit_max_tracked_ips must be >= 1 (got {self.rate_limit_max_tracked_ips})"
            raise ValueError(msg)
        if self.forwarded_for_trusted_hops < 1:
            msg = f"forwarded_for_trusted_hops must be >= 1 (got {self.forwarded_for_trusted_hops})"
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

    def to_iic_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict for IIC transfer.

        Drops non-serializable fields (callables, middleware, display).
        Converts frozenset to sorted list for JSON round-tripping.

        """
        import dataclasses

        d: dict[str, object] = {}
        for f in dataclasses.fields(self):
            if f.name in _IIC_SKIP_FIELDS:
                continue
            val = getattr(self, f.name)
            if isinstance(val, frozenset):
                val = sorted(val)
            d[f.name] = val
        return d

    def to_json(self) -> str:
        """Serialize to a JSON string for IIC transfer."""
        import json

        return json.dumps(self.to_iic_dict())

    @classmethod
    def from_iic_dict(cls, d: dict[str, Any]) -> ServerConfig:
        """Reconstruct from a dict produced by :meth:`to_iic_dict`.

        Converts list back to frozenset for ``trusted_hosts``, and
        drops any keys that are not valid constructor parameters.

        """
        import dataclasses

        valid_names = {f.name for f in dataclasses.fields(cls)} - _IIC_SKIP_FIELDS
        filtered = {k: v for k, v in d.items() if k in valid_names}

        # Convert list back to frozenset for trusted_hosts
        if "trusted_hosts" in filtered and isinstance(filtered["trusted_hosts"], list):
            filtered["trusted_hosts"] = frozenset(filtered["trusted_hosts"])

        # Convert list back to tuple for tuple fields
        for name in ("reload_include", "reload_dirs"):
            if name in filtered and isinstance(filtered[name], list):
                filtered[name] = tuple(filtered[name])

        return cls(**filtered)

    @classmethod
    def from_json(cls, s: str) -> ServerConfig:
        """Reconstruct from a JSON string produced by :meth:`to_json`."""
        import json

        return cls.from_iic_dict(json.loads(s))

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> ServerConfig:
        """Build a ``ServerConfig`` from an untyped key/value mapping.

        This is the typed construction site for merged config dicts (e.g. the
        output of :func:`pounce._config_file.load_config_with_overrides`, which
        is ``dict[str, Any]``). It owns the single unavoidable cast from an
        ``object``-valued mapping to the keyword-only constructor, so callers
        can splat config without sprinkling per-site type-ignore comments.

        Validation is unchanged from direct construction: unknown keys raise
        ``TypeError`` and out-of-range/invalid values raise ``ValueError`` via
        :meth:`__post_init__`.

        """
        return cls(**dict(mapping))
