"""
Built-in dictionary serving endpoint for RFC 9842 compression dictionaries.

Serves dictionary files at ``/.well-known/compression-dictionary/<hash>``
so clients can download dictionaries advertised via ``Use-As-Dictionary``.

Also provides ``use_as_dictionary_headers`` for injecting the
``Use-As-Dictionary`` response header on matching paths, enabling
browser dictionary discovery.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pounce._compression import _match_pattern

if TYPE_CHECKING:
    from pounce._compression import CompressionDictionary

# Standard path prefix for dictionary serving (RFC 9842 §4)
DICTIONARY_PATH_PREFIX = "/.well-known/compression-dictionary/"


def build_dictionary_response(
    dictionaries: tuple[CompressionDictionary, ...],
    path: str,
) -> tuple[int, list[tuple[bytes, bytes]], bytes] | None:
    """Build a response serving a dictionary file by its sf-hash.

    Args:
        dictionaries: Loaded server dictionaries.
        path: Request path (e.g. ``/.well-known/compression-dictionary/:abc=:``).

    Returns:
        ``(status, headers, body)`` if the path matches a dictionary,
        or ``None`` if the path doesn't match the dictionary prefix.
    """
    if not path.startswith(DICTIONARY_PATH_PREFIX):
        return None

    requested_hash = path[len(DICTIONARY_PATH_PREFIX) :]
    if not requested_hash:
        return _not_found()

    for d in dictionaries:
        if d.sf_hash == requested_hash:
            body = d.zstd_dict.dict_content
            headers: list[tuple[bytes, bytes]] = [
                (b"content-type", b"application/dictionary"),
                (b"content-length", str(len(body)).encode("ascii")),
                # Dictionaries are immutable — cache aggressively
                (b"cache-control", b"public, max-age=604800, immutable"),
            ]
            return 200, headers, body

    return _not_found()


def use_as_dictionary_headers(
    dictionaries: tuple[CompressionDictionary, ...],
    request_target: str,
) -> list[tuple[bytes, bytes]]:
    """Return ``Use-As-Dictionary`` headers for matching dictionaries.

    For each dictionary whose ``match`` pattern covers the request target,
    returns a header telling the browser to fetch and cache the dictionary
    for future requests to matching URLs.

    Args:
        dictionaries: Loaded server dictionaries.
        request_target: The request URL path.

    Returns:
        List of ``(name, value)`` header tuples (may be empty).
    """
    headers: list[tuple[bytes, bytes]] = []
    for d in dictionaries:
        if not d.match:
            continue
        if _match_pattern(d.match, request_target):
            # RFC 9842 §3.2: Use-As-Dictionary header with match parameter
            value = f'match="{d.match}"'.encode("ascii")
            headers.append((b"use-as-dictionary", value))
            # Also advertise where to fetch this dictionary
            dict_url = f"{DICTIONARY_PATH_PREFIX}{d.sf_hash}".encode("ascii")
            headers.append((b"link", b"<" + dict_url + b'>; rel="dictionary"'))
    return headers


def _not_found() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    return (
        404,
        [(b"content-type", b"text/plain"), (b"content-length", b"9")],
        b"Not Found",
    )
