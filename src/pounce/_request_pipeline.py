"""
Shared request pipeline — functions used by both Worker and SyncWorker.

Eliminates duplication across the two worker types and ensures feature
parity (access log filter, duration tracking, request ID propagation).

"""

from collections.abc import Callable, Sequence
from typing import Any

from pounce._compression import (
    CompressionDictionary,
    Compressor,
    create_compressor,
    negotiate_dictionary,
    negotiate_encoding,
)
from pounce._headers import get_header
from pounce._request_id import extract_or_generate
from pounce.asgi.bridge import build_scope
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols._base import RequestReceived


def is_trusted_peer(config: ServerConfig, client_addr: str) -> bool:
    """Check if the client address is in the trusted hosts set."""
    return bool(
        config.trusted_hosts
        and (config.trusted_hosts_wildcard or client_addr in config.trusted_hosts)
    )


def prepare_request(
    request: RequestReceived,
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
    lifespan_state: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Build ASGI scope, extract request ID, and set extensions.

    Returns (scope, request_id).
    """
    scope = build_scope(request, config, client, server, state=lifespan_state)
    trusted = is_trusted_peer(config, client[0])
    request_id = extract_or_generate(request.headers, trusted=trusted)
    scope.setdefault("extensions", {})["request_id"] = request_id
    return scope, request_id


def negotiate_compressor(
    config: ServerConfig,
    headers: Sequence[tuple[bytes, bytes]],
    *,
    request_target: str = "",
) -> tuple[Compressor | None, CompressionDictionary | None]:
    """Negotiate content-encoding compression from request headers.

    Returns (compressor, dictionary) — dictionary is non-None only when
    ``dcz`` (dictionary-compressed zstd) encoding is selected.
    """
    if not config.compression:
        return None, None
    accept_enc = get_header(headers, b"accept-encoding")
    if not accept_enc:
        return None, None

    # Check for dictionary compression (RFC 9842)
    if config.compression_dictionaries and b"zstd" in accept_enc:
        avail_dict = get_header(headers, b"available-dictionary")
        if avail_dict:
            dictionary = negotiate_dictionary(
                avail_dict,
                config.compression_dictionaries,
                request_target,
            )
            if dictionary is not None:
                return create_compressor("dcz", dictionary=dictionary), dictionary

    # Standard encoding negotiation
    enc = negotiate_encoding(accept_enc)
    if not enc:
        return None, None
    return create_compressor(enc), None


def log_request(
    config: ServerConfig,
    method: str,
    target: str,
    status: int,
    bytes_sent: int,
    duration_ms: float,
    client_str: str,
    *,
    http_version: str = "1.1",
    request_id: str | None = None,
    worker_id: int | None = None,
) -> None:
    """Log an access log entry, respecting the access_log_filter."""
    if not config.access_log:
        return
    log_filter: Callable[[str, str, int], bool] | None = config.access_log_filter
    if log_filter is not None and not log_filter(method, target, status):
        return
    access_log(
        method,
        target,
        status,
        bytes_sent,
        duration_ms,
        client_str,
        http_version=http_version,
        request_id=request_id,
        worker_id=worker_id,
    )
