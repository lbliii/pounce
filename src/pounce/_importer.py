"""
Application importer — resolves string references to ASGI callables.

Handles the standard "module:attribute" pattern used by ASGI servers:

    "myapp:app"              → import myapp; return myapp.app
    "myapp.web:app"          → import myapp.web; return myapp.web.app
    "myapp:create_app()"     → import myapp; return myapp.create_app()

For development mode, :func:`reimport_app` clears project-local modules
from ``sys.modules`` before re-importing, so that code changes on disk
are picked up without a full process restart.

"""

import importlib
import logging
import os
import sys
from typing import Any

from pounce._types import ASGIApp

logger = logging.getLogger("pounce.reload")


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
        raise ValueError(f"Invalid app path {app_path!r}. Module name is empty.")

    if not attr_path:
        raise ValueError(f"Invalid app path {app_path!r}. Attribute name is empty.")

    # Detect factory pattern: "module:factory()"
    is_factory = attr_path.endswith("()")
    if is_factory:
        attr_path = attr_path[:-2]

    if not attr_path:
        raise ValueError(f"Invalid app path {app_path!r}. Attribute name is empty.")

    # Import the module
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(f"Could not import module {module_path!r}. {exc}") from exc

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
                f"Factory {module_path}:{attr_path} is not callable (got {type(obj).__name__})."
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


def reimport_app(app_path: str, base_dirs: list[str] | None = None) -> ASGIApp:
    """Re-import an ASGI app, clearing cached local modules first.

    Evicts all ``sys.modules`` entries whose source file lives under one
    of the *base_dirs* (defaults to the current working directory), then
    re-imports the application via :func:`import_app`.  This forces
    Python to re-execute module source, picking up code changes saved
    since the last load.

    Args:
        app_path: Application reference in ``"module:attribute"`` format.
        base_dirs: Directories whose modules should be evicted from the
            module cache.  Defaults to ``[os.getcwd()]``.

    Returns:
        A freshly imported ASGI application callable.

    Raises:
        Same exceptions as :func:`import_app` — ``ValueError``,
        ``ImportError``, ``AttributeError``, ``TypeError``.

    """
    cleared = _clear_local_modules(base_dirs)
    if cleared:
        logger.info(
            "Cleared %d cached module(s) — reimporting %s",
            len(cleared),
            app_path,
        )
    return import_app(app_path)


def _clear_local_modules(base_dirs: list[str] | None = None) -> list[str]:
    """Remove project-local modules from ``sys.modules``.

    A module is considered "local" when its ``__file__`` attribute
    resolves to a path under one of the *base_dirs*.  Third-party
    packages (installed in site-packages) and the standard library are
    left untouched.

    Args:
        base_dirs: Directories to treat as project roots.
            Defaults to ``[os.getcwd()]``.

    Returns:
        Sorted list of module names that were removed.

    """
    if base_dirs is None:
        base_dirs = [os.getcwd()]

    # Resolve symlinks and normalise to absolute paths with trailing
    # separator so that ``str.startswith()`` doesn't false-match partial
    # directory names (e.g. /app matching /application).  ``realpath``
    # is critical on macOS where /var → /private/var.
    prefixes = tuple(os.path.realpath(d) + os.sep for d in base_dirs)

    to_remove: list[str] = []
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        filepath = getattr(mod, "__file__", None)
        if filepath is None:
            continue
        try:
            real_path = os.path.realpath(filepath)
        except (TypeError, ValueError):
            continue
        if real_path.startswith(prefixes):
            to_remove.append(name)

    for name in to_remove:
        # Delete the bytecode cache (.pyc) so Python recompiles from
        # the updated source instead of reusing a stale cached file.
        mod = sys.modules[name]
        cached = getattr(mod, "__cached__", None)
        if cached:
            try:
                os.unlink(cached)
            except OSError:
                pass
        del sys.modules[name]

    # Tell the import machinery to re-check the filesystem.
    importlib.invalidate_caches()

    return sorted(to_remove)
