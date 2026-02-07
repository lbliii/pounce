"""
Server configuration.

ServerConfig is the single configuration object for a pounce server instance.
Frozen after creation — the server reads config but never mutates it.

"""

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

    # Logging
    access_log: bool = True
    log_level: str = "info"

    # HTTP
    server_header: str = "pounce"
    date_header: bool = True

    # Development
    reload: bool = False

    # Headers to trust from proxy (empty = direct connection)
    trusted_hosts: tuple[str, ...] = field(default_factory=tuple)

    # TLS (optional — phase 3)
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
