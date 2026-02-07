"""
Error hierarchy for pounce.

All pounce errors inherit from PounceError. Each error maps to an HTTP status
code for automatic error response generation.

Error categories:
- ParseError: malformed HTTP from the client (400)
- TimeoutError: request or keep-alive timeout (408)
- LimitError: headers or body exceed configured limits (413/431)
- AppError: the ASGI application raised an exception (500)
- LifespanError: lifespan startup or shutdown failure (500)
- SupervisorError: worker spawn or crash-restart failure (500)
- WorkerError: worker-level failure reported to supervisor (500)
- TLSError: TLS configuration or handshake failure (500)
- ReloadError: file-watcher or worker-restart failure during reload (500)

"""


class PounceError(Exception):
    """Base exception for all pounce server errors."""

    status_code: int = 500

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class ParseError(PounceError):
    """Malformed HTTP request — h11 could not parse the input."""

    status_code: int = 400


class TimeoutError(PounceError):
    """Request or keep-alive timeout exceeded."""

    status_code: int = 408


class LimitError(PounceError):
    """Request headers or body exceed configured size limits."""

    status_code: int = 413

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        # 431 for header-specific limits, 413 for body
        super().__init__(message, status_code=status_code)


class AppError(PounceError):
    """The ASGI application raised an unhandled exception."""

    status_code: int = 500


class LifespanError(PounceError):
    """ASGI lifespan startup or shutdown failed."""

    status_code: int = 500


class SupervisorError(PounceError):
    """Worker spawn failure or crash-restart exhaustion."""

    status_code: int = 500


class WorkerError(PounceError):
    """Worker-level failure that bubbles to the supervisor."""

    status_code: int = 500


class TLSError(PounceError):
    """TLS configuration or handshake failure."""

    status_code: int = 500


class ReloadError(PounceError):
    """File-watcher or worker-restart failure during reload."""

    status_code: int = 500
