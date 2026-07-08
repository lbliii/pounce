"""
Shared request pipeline — functions used by both Worker and SyncWorker.

Eliminates duplication across the two worker types and ensures feature
parity (access log filter, duration tracking, request ID propagation).

"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pounce._compression import (
    CompressionDictionary,
    Compressor,
    create_compressor,
    negotiate_dictionary,
    negotiate_encoding,
)
from pounce._dictionary_endpoint import build_dictionary_response
from pounce._headers import get_header
from pounce._health import build_health_response
from pounce._introspect import build_introspect_response
from pounce._request_id import extract_or_generate
from pounce.asgi.bridge import build_scope
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols._base import RequestReceived

type _IntProvider = int | Callable[[], int]
type _BoolProvider = bool | Callable[[], bool]


@dataclass(frozen=True, slots=True)
class BuiltinResponse:
    """A protocol-neutral response produced by a built-in endpoint."""

    kind: Literal["health", "introspection", "dictionary"]
    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes


def _resolve_int_provider(value: _IntProvider) -> int:
    return value if isinstance(value, int) else value()


def _resolve_bool_provider(value: _BoolProvider) -> bool:
    return value if isinstance(value, bool) else value()


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


def maybe_build_builtin_response(
    config: ServerConfig,
    method: str | bytes,
    path: str,
    *,
    worker_id: int,
    active_connections: _IntProvider,
    draining: _BoolProvider = False,
) -> BuiltinResponse | None:
    """Select and build a built-in endpoint response before ASGI dispatch.

    Endpoint selection is shared across every HTTP transport while response
    serialization remains protocol-owned. Providers are evaluated lazily so
    normal application requests do not acquire connection-count locks or call
    drain-state hooks on the latency-sensitive request path.
    """
    if method not in ("GET", b"GET", "HEAD", b"HEAD"):
        return None

    if config.health_check_path is not None and path == config.health_check_path:
        status, headers, body = build_health_response(
            worker_id=worker_id,
            active_connections=_resolve_int_provider(active_connections),
            draining=_resolve_bool_provider(draining),
        )
        return BuiltinResponse("health", status, headers, body)

    if config.introspection_enabled and path == config.introspection_path:
        status, headers, body = build_introspect_response(
            config=config,
            worker_id=worker_id,
            active_connections=_resolve_int_provider(active_connections),
        )
        return BuiltinResponse("introspection", status, headers, body)

    if config.compression_dictionaries:
        dictionary_response = build_dictionary_response(config.compression_dictionaries, path)
        if dictionary_response is not None:
            status, headers, body = dictionary_response
            return BuiltinResponse("dictionary", status, headers, body)

    return None


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
    # ``available-dictionary`` is only consulted when a dcz negotiation is
    # possible; the shared core re-checks the guards, so eager extraction here
    # is harmless and keeps a single negotiation implementation.
    avail_dict = get_header(headers, b"available-dictionary")
    return negotiate_compressor_from_meta(
        config,
        accept_enc,
        avail_dict,
        request_target=request_target,
    )


def negotiate_compressor_from_meta(
    config: ServerConfig,
    accept_encoding: bytes | None,
    available_dictionary: bytes | None,
    *,
    request_target: str = "",
) -> tuple[Compressor | None, CompressionDictionary | None]:
    """Negotiate compression from already-extracted header values.

    Meta-keyed entry point for the sync-worker hot path: the caller passes the
    ``accept-encoding`` and ``available-dictionary`` values already pulled out
    of ``_RequestMeta`` (no redundant header scan) plus the decoded request
    target. Behaviour is identical to :func:`negotiate_compressor`.

    Returns (compressor, dictionary) — dictionary is non-None only when
    ``dcz`` (dictionary-compressed zstd) encoding is selected.
    """
    if not config.compression or not accept_encoding:
        return None, None

    # Check for dictionary compression (RFC 9842)
    if config.compression_dictionaries and available_dictionary and b"zstd" in accept_encoding:
        dictionary = negotiate_dictionary(
            available_dictionary,
            config.compression_dictionaries,
            request_target,
        )
        if dictionary is not None:
            return create_compressor("dcz", dictionary=dictionary), dictionary

    # Standard encoding negotiation
    enc = negotiate_encoding(accept_encoding)
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
