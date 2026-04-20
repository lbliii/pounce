"""
Error hierarchy for pounce.

All pounce errors inherit from PounceError. Each error maps to an HTTP status
code for automatic error response generation.

Error categories:
- ParseError: malformed HTTP from the client (400)
- RequestTimeoutError: request or keep-alive timeout (408)
- LimitError: headers or body exceed configured limits (413/431)
- AppError: the ASGI application raised an exception (500)
- LifespanError: lifespan startup or shutdown failure (500)
- SupervisorError: worker spawn or crash-restart failure (500)
- WorkerError: worker-level failure reported to supervisor (500)
- TLSError: TLS configuration or handshake failure (500)
- ReloadError: file-watcher or worker-restart failure during reload (500)

Every error carries a semantic ``code`` of the form ``POUNCE_<CATEGORY>_<SPECIFIC>``
and an optional ``hint`` with actionable remediation. See
docs/design/error-codes.md for the naming scheme.

"""


class PounceError(Exception):
    """Base exception for all pounce server errors.

    Args:
        message: Human-readable error message.
        status_code: HTTP status code override. Defaults to the class attribute.
        code: Semantic error code ``POUNCE_<CATEGORY>_<SPECIFIC>``. Defaults to
            the class's ``default_code``. Stable identifier safe for log keying
            and response headers.
        hint: Optional actionable remediation ("Pass --ssl-certfile=PATH ...").
        doc: Docs anchor. Defaults to ``docs/troubleshooting.md#<code>`` — the
            catalog coverage test guarantees every code has a heading at that
            anchor. Pass explicitly only to override.
    """

    status_code: int = 500
    default_code: str = "POUNCE_E_UNKNOWN"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        hint: str | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        self.code = code if code is not None else self.default_code
        self.hint = hint
        self.doc = doc if doc is not None else f"docs/troubleshooting.md#{self.code}"

    def __reduce__(self) -> tuple[object, ...]:
        # Preserve code/hint/doc across pickle (process-worker mode).
        return (
            _reconstruct_pounce_error,
            (type(self), str(self), self.status_code, self.code, self.hint, self.doc),
        )


def _reconstruct_pounce_error(
    cls: type[PounceError],
    message: str,
    status_code: int,
    code: str,
    hint: str | None,
    doc: str | None,
) -> PounceError:
    return cls(message, status_code=status_code, code=code, hint=hint, doc=doc)


class ParseError(PounceError):
    """Malformed HTTP request — h11 could not parse the input."""

    status_code: int = 400
    default_code: str = "POUNCE_PARSE_E"


class RequestTimeoutError(PounceError):
    """Request or keep-alive timeout exceeded.

    Named ``RequestTimeoutError`` to avoid shadowing the builtin
    ``TimeoutError`` (which is a subclass of ``OSError``).
    """

    status_code: int = 408
    default_code: str = "POUNCE_TIMEOUT_E"


class LimitError(PounceError):
    """Request headers or body exceed configured size limits.

    Default status is 413 (Content Too Large). Pass ``status_code=431``
    for header-specific limits (Request Header Fields Too Large).
    """

    status_code: int = 413
    default_code: str = "POUNCE_LIMIT_E"


class AppError(PounceError):
    """The ASGI application raised an unhandled exception."""

    status_code: int = 500
    default_code: str = "POUNCE_APP_E"


class LifespanError(PounceError):
    """ASGI lifespan startup or shutdown failed."""

    status_code: int = 500
    default_code: str = "POUNCE_LIFESPAN_E"


class SupervisorError(PounceError):
    """Worker spawn failure or crash-restart exhaustion."""

    status_code: int = 500
    default_code: str = "POUNCE_SUPERVISOR_E"


class WorkerError(PounceError):
    """Worker-level failure that bubbles to the supervisor."""

    status_code: int = 500
    default_code: str = "POUNCE_WORKER_E"


class TLSError(PounceError):
    """TLS configuration or handshake failure."""

    status_code: int = 500
    default_code: str = "POUNCE_TLS_E"


class ReloadError(PounceError):
    """File-watcher or worker-restart failure during reload."""

    status_code: int = 500
    default_code: str = "POUNCE_RELOAD_E"
