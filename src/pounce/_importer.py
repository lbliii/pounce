"""
Application importer — resolves string references to ASGI callables.

Handles the standard "module:attribute" pattern used by ASGI servers:

    "myapp:app"              → import myapp; return myapp.app
    "myapp.web:app"          → import myapp.web; return myapp.web.app
    "myapp:create_app()"     → import myapp; return myapp.create_app()

"""

import importlib
from typing import Any

from pounce._types import ASGIApp


def import_app(app_path: str) -> ASGIApp:
    """Resolve an application string to an ASGI callable.

    Args:
        app_path: Application reference in "module:attribute" format.
            Optionally, the attribute may end with "()" to call a factory.

    Returns:
        The ASGI application callable.

    Raises:
        ValueError: If the format is invalid (missing colon separator).
        ImportError: If the module cannot be imported.
        AttributeError: If the attribute does not exist on the module.
        TypeError: If the resolved object is not callable.

    Example:
        >>> app = import_app("myapp:app")
        >>> app = import_app("myapp.web:create_app()")

    """
    if ":" not in app_path:
        raise ValueError(
            f"Invalid app path {app_path!r}. "
            "Expected format: 'module:attribute' (e.g., 'myapp:app')."
        )

    module_path, _, attr_path = app_path.partition(":")

    if not module_path:
        raise ValueError(
            f"Invalid app path {app_path!r}. Module name is empty."
        )

    if not attr_path:
        raise ValueError(
            f"Invalid app path {app_path!r}. Attribute name is empty."
        )

    # Detect factory pattern: "module:factory()"
    is_factory = attr_path.endswith("()")
    if is_factory:
        attr_path = attr_path[:-2]

    if not attr_path:
        raise ValueError(
            f"Invalid app path {app_path!r}. Attribute name is empty."
        )

    # Import the module
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"Could not import module {module_path!r}. {exc}"
        ) from exc

    # Resolve the attribute (supports dotted paths like "sub.app")
    obj: Any = module
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise AttributeError(
                f"Module {module_path!r} has no attribute {attr_path!r}. {exc}"
            ) from exc

    # Call factory if requested
    if is_factory:
        if not callable(obj):
            raise TypeError(
                f"Factory {module_path}:{attr_path} is not callable "
                f"(got {type(obj).__name__})."
            )
        obj = obj()

    # Validate the result is callable
    if not callable(obj):
        raise TypeError(
            f"Application {module_path}:{attr_path} is not callable "
            f"(got {type(obj).__name__}). "
            "Expected an ASGI application (async callable)."
        )

    return obj  # type: ignore[return-value]
