"""
Server configuration.

ServerConfig is the single configuration object for a pounce server instance.
Frozen after creation — the server reads config but never mutates it.

"""

from __future__ import annotations

from dataclasses import dataclass, field


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

    # Timeouts (seconds)
    keep_alive_timeout: float = 5.0
    request_timeout: float = 30.0
    shutdown_timeout: float = 10.0

    # Limits
    max_request_size: int = 1_048_576  # 1 MB
    max_header_size: int = 65_536      # 64 KB
    max_headers: int = 100
    max_connections: int = 10_000

    # Logging
    access_log: bool = True
    log_level: str = "info"

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
    reload: bool = False

    # h11 tuning
    h11_max_incomplete_event_size: int | None = None  # None = h11 default (16 KB)

    # Headers to trust from proxy (empty = direct connection)
    trusted_hosts: tuple[str, ...] = field(default_factory=tuple)

    # TLS (optional — phase 3)
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.workers < 0:
            msg = f"workers must be >= 0 (got {self.workers})"
            raise ValueError(msg)
        if self.port < 0 or self.port > 65535:
            msg = f"port must be 0-65535 (got {self.port})"
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
