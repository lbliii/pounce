"""
Sentry error tracking integration for pounce.

Optional integration with Sentry SDK for automatic error reporting,
performance monitoring, and request context capture.

"""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("pounce.sentry")


def is_sentry_available() -> bool:
    """Check if Sentry SDK is installed.

    Returns:
        True if sentry_sdk is available, False otherwise

    """
    try:
        import sentry_sdk  # noqa: F401

        return True
    except ImportError:
        return False


def init_sentry(
    dsn: str,
    *,
    environment: str | None = None,
    release: str | None = None,
    traces_sample_rate: float = 0.1,
    profiles_sample_rate: float = 0.1,
    server_name: str | None = None,
    debug: bool = False,
) -> None:
    """Initialize Sentry SDK.

    Args:
        dsn: Sentry DSN (Data Source Name)
        environment: Environment name (e.g., "production", "staging")
        release: Release version (e.g., "myapp@1.0.0")
        traces_sample_rate: Sample rate for performance monitoring (0.0-1.0)
        profiles_sample_rate: Sample rate for profiling (0.0-1.0)
        server_name: Server name for tagging
        debug: Enable debug mode

    Example:
        init_sentry(
            dsn="https://examplePublicKey@o0.ingest.sentry.io/0",
            environment="production",
            release="myapp@1.0.0",
            traces_sample_rate=0.1,
        )

    """
    if not is_sentry_available():
        msg = "sentry-sdk not installed. Install with: pip install sentry-sdk"
        raise ImportError(msg)

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    # Configure logging integration
    logging_integration = LoggingIntegration(
        level=logging.INFO,  # Capture info and above as breadcrumbs
        event_level=logging.ERROR,  # Send errors as events
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        server_name=server_name,
        debug=debug,
        integrations=[logging_integration],
        # Capture request bodies (useful for debugging)
        max_request_body_size="medium",
        # Send default PII (can be disabled for compliance)
        send_default_pii=True,
    )

    logger.info("Sentry initialized: environment=%s release=%s", environment, release)


def create_sentry_wrapper(app: Callable) -> Callable:
    """Wrap an ASGI app with Sentry error tracking.

    Automatically captures exceptions, request context, and performance data.

    Args:
        app: Original ASGI app

    Returns:
        Wrapped ASGI app with Sentry integration

    Example:
        app = create_sentry_wrapper(app)

    """
    if not is_sentry_available():
        logger.warning("Sentry SDK not available, error tracking disabled")
        return app

    from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

    # Use Sentry's built-in ASGI middleware
    return SentryAsgiMiddleware(app)


def capture_exception(
    error: Exception,
    *,
    level: str = "error",
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Manually capture an exception to Sentry.

    Args:
        error: Exception to capture
        level: Severity level ("fatal", "error", "warning", "info", "debug")
        tags: Tags to attach to event
        extra: Extra context data

    Returns:
        Event ID if captured, None if Sentry unavailable

    Example:
        try:
            risky_operation()
        except Exception as e:
            capture_exception(
                e,
                tags={"component": "database"},
                extra={"query": sql},
            )

    """
    if not is_sentry_available():
        return None

    import sentry_sdk

    with sentry_sdk.push_scope() as scope:
        scope.level = level

        if tags:
            for key, value in tags.items():
                scope.set_tag(key, value)

        if extra:
            for key, value in extra.items():
                scope.set_extra(key, value)

        return sentry_sdk.capture_exception(error)


def capture_message(
    message: str,
    *,
    level: str = "info",
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Manually capture a message to Sentry.

    Args:
        message: Message to capture
        level: Severity level ("fatal", "error", "warning", "info", "debug")
        tags: Tags to attach to event
        extra: Extra context data

    Returns:
        Event ID if captured, None if Sentry unavailable

    Example:
        capture_message(
            "Unusual activity detected",
            level="warning",
            tags={"component": "auth"},
            extra={"user_id": user.id},
        )

    """
    if not is_sentry_available():
        return None

    import sentry_sdk

    with sentry_sdk.push_scope() as scope:
        scope.level = level

        if tags:
            for key, value in tags.items():
                scope.set_tag(key, value)

        if extra:
            for key, value in extra.items():
                scope.set_extra(key, value)

        return sentry_sdk.capture_message(message, level=level)


def add_breadcrumb(
    message: str,
    *,
    category: str | None = None,
    level: str = "info",
    data: dict[str, Any] | None = None,
) -> None:
    """Add a breadcrumb for context.

    Breadcrumbs are added to error reports for debugging context.

    Args:
        message: Breadcrumb message
        category: Category (e.g., "auth", "database", "http")
        level: Severity level
        data: Additional data

    Example:
        add_breadcrumb(
            "User logged in",
            category="auth",
            level="info",
            data={"user_id": user.id},
        )

    """
    if not is_sentry_available():
        return

    import sentry_sdk

    sentry_sdk.add_breadcrumb(
        message=message,
        category=category,
        level=level,
        data=data,
    )


def set_user(
    user_id: str | None = None,
    *,
    email: str | None = None,
    username: str | None = None,
    ip_address: str | None = None,
    **extra: Any,
) -> None:
    """Set user context for error reports.

    Args:
        user_id: User ID
        email: User email
        username: Username
        ip_address: User IP address
        **extra: Additional user data

    Example:
        set_user(
            user_id=str(user.id),
            email=user.email,
            username=user.username,
        )

    """
    if not is_sentry_available():
        return

    import sentry_sdk

    user_data = {}
    if user_id is not None:
        user_data["id"] = user_id
    if email is not None:
        user_data["email"] = email
    if username is not None:
        user_data["username"] = username
    if ip_address is not None:
        user_data["ip_address"] = ip_address

    user_data.update(extra)

    sentry_sdk.set_user(user_data)


def set_tag(key: str, value: Any) -> None:
    """Set a tag for the current scope.

    Args:
        key: Tag key
        value: Tag value

    Example:
        set_tag("deployment", "blue")

    """
    if not is_sentry_available():
        return

    import sentry_sdk

    sentry_sdk.set_tag(key, value)


def set_context(key: str, value: dict[str, Any]) -> None:
    """Set context data for the current scope.

    Args:
        key: Context key
        value: Context data (must be dict)

    Example:
        set_context("database", {
            "query": sql,
            "duration_ms": 150,
        })

    """
    if not is_sentry_available():
        return

    import sentry_sdk

    sentry_sdk.set_context(key, value)


def start_transaction(
    name: str,
    op: str = "http.server",
) -> Any:
    """Start a performance transaction.

    Args:
        name: Transaction name (e.g., "GET /api/users")
        op: Operation type

    Returns:
        Transaction context manager

    Example:
        with start_transaction("process_payment", op="payment"):
            process_payment(order)

    """
    if not is_sentry_available():
        # Return a no-op context manager
        class NoOpTransaction:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return NoOpTransaction()

    import sentry_sdk

    return sentry_sdk.start_transaction(name=name, op=op)


def flush(timeout: float = 2.0) -> bool:
    """Flush pending events to Sentry.

    Useful before shutdown to ensure all events are sent.

    Args:
        timeout: Maximum time to wait (seconds)

    Returns:
        True if all events were flushed, False if timeout

    Example:
        # Before shutdown
        flush(timeout=5.0)

    """
    if not is_sentry_available():
        return True

    import sentry_sdk

    client = sentry_sdk.Hub.current.client
    if client is not None:
        return client.flush(timeout=timeout)

    return True
