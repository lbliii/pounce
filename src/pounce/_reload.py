"""
File watcher for development mode (``--reload``).

Polls the application's source directory for changes and signals the
supervisor to restart workers when modifications are detected.

Uses stdlib ``pathlib`` + polling. Ignores ``__pycache__``, ``.git``,
``node_modules``, and common virtual environment directories.

Optionally uses ``watchfiles`` (Rust-based, fast) when available.

"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("pounce.reload")

# Directories to always exclude from watching
_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
})

# File extensions to watch
_WATCH_EXTENSIONS: frozenset[str] = frozenset({
    ".py",
    ".pyi",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".cfg",
    ".ini",
})


def _should_watch(path: Path) -> bool:
    """Check if a path should be watched for changes."""
    # Skip excluded directories
    for part in path.parts:
        if part in _EXCLUDE_DIRS:
            return False
    # Only watch specific extensions
    return path.suffix in _WATCH_EXTENSIONS


def _snapshot(directories: list[Path]) -> dict[str, float]:
    """Take a snapshot of file modification times.

    Returns:
        Dict mapping file path strings to their mtime.

    """
    snapshot: dict[str, float] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and _should_watch(path):
                try:
                    snapshot[str(path)] = path.stat().st_mtime
                except OSError:
                    pass
    return snapshot


def detect_changes(
    directories: list[Path],
    previous: dict[str, float],
) -> tuple[set[str], dict[str, float]]:
    """Compare current state against a previous snapshot.

    Args:
        directories: Directories to scan.
        previous: Previous snapshot from ``_snapshot()``.

    Returns:
        Tuple of (changed_files, new_snapshot).

    """
    current = _snapshot(directories)
    changed: set[str] = set()

    # Check for new or modified files
    for path, mtime in current.items():
        if path not in previous or previous[path] != mtime:
            changed.add(path)

    # Check for deleted files
    for path in previous:
        if path not in current:
            changed.add(path)

    return changed, current


def watch_for_changes(
    directories: list[Path],
    callback: object,  # Callable[[], None] — typed as object to avoid Protocol import
    *,
    interval: float = 1.0,
    stop_event: object | None = None,  # threading.Event
) -> None:
    """Poll directories for changes and call callback on detection.

    This is a blocking function designed to run in a thread. It polls
    at the given interval and calls ``callback()`` whenever changes
    are detected.

    Args:
        directories: Directories to watch.
        callback: Called (with no args) when changes are detected.
        interval: Polling interval in seconds (default: 1.0).
        stop_event: Optional threading.Event to stop the watcher.

    """
    import threading

    if stop_event is None:
        stop_event = threading.Event()

    logger.info(
        "Watching %d directories for changes (interval: %.1fs)",
        len(directories), interval,
    )

    snapshot = _snapshot(directories)

    while not stop_event.is_set():  # type: ignore[union-attr]
        time.sleep(interval)

        if stop_event.is_set():  # type: ignore[union-attr]
            break

        changed, snapshot = detect_changes(directories, snapshot)
        if changed:
            # Log the first few changed files
            file_list = sorted(changed)[:5]
            logger.info(
                "Detected %d changed file(s): %s%s",
                len(changed),
                ", ".join(os.path.basename(f) for f in file_list),
                "..." if len(changed) > 5 else "",
            )
            callback()  # type: ignore[operator]

    logger.info("File watcher stopped")
