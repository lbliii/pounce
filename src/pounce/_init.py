"""
``pounce init`` — scaffold a minimal pounce project in the current directory.

Drops three files and refuses to overwrite without ``--force``:

- ``app.py`` — vanilla ASGI "hello from pounce" (no framework)
- ``pounce.toml`` — commented template of every ``ServerConfig`` default,
  generated via :func:`pounce._config_schema.build_toml_template`
- ``.gitignore`` — the three lines every Python project needs

See ``docs/design/init-scope.md`` for why this is vanilla-only.
"""

from __future__ import annotations

from pathlib import Path

from pounce._config_schema import build_toml_template

#: The vanilla ASGI app template — pure ASGI, no framework. The leading
#: docstring is a tour guide: a fresh agent reading this file finds every
#: other command pounce ships without leaving the file. Signpost budget
#: (enforced by ``tests/unit/test_init.py::TestAppTemplateSignposts``): ≤15
#: non-blank docstring lines.
APP_TEMPLATE = '''\
"""Minimal ASGI app. Replace me with your real app.

Run it:
    pounce serve --app app:app      # start the server
    pounce check --app app:app      # pre-flight validation (same flags as serve)
    pounce info                     # Python, GIL, deps, installed frameworks

Inspect config:
    pounce config schema            # every setting (JSON or TOML template)
    pounce config show              # print the resolved merged config

Talk to agents:
    pounce --mcp                    # run pounce as an MCP server on stdin/stdout

Troubleshoot:
    docs/troubleshooting.md — every POUNCE_* error code, with context + remedy
"""


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"hello from pounce\\n",
        }
    )
'''

GITIGNORE_TEMPLATE = """\
__pycache__/
*.pyc
.pounce/
"""

#: The three file names written by ``pounce init``, in stable order so the
#: collision message and write sequence are predictable.
SCAFFOLD_FILES: tuple[str, ...] = ("app.py", "pounce.toml", ".gitignore")


class InitError(Exception):
    """Raised when the scaffold cannot be written (e.g. file collisions)."""

    def __init__(self, message: str, *, colliding: list[str] | None = None) -> None:
        super().__init__(message)
        self.colliding = colliding or []


def _existing_files(directory: Path) -> list[str]:
    """Return scaffold filenames that already exist in *directory*."""
    return [name for name in SCAFFOLD_FILES if (directory / name).exists()]


def run_init(directory: Path, *, force: bool = False) -> list[Path]:
    """Write the scaffold into *directory*.

    Args:
        directory: Target directory (must exist).
        force: Overwrite existing scaffold files if True.

    Returns:
        The list of paths written, in the order of :data:`SCAFFOLD_FILES`.

    Raises:
        InitError: If ``directory`` does not exist, or if any scaffold file
            already exists and *force* is False.
    """
    if not directory.exists():
        raise InitError(f"Directory does not exist: {directory}")
    if not directory.is_dir():
        raise InitError(f"Not a directory: {directory}")

    collisions = _existing_files(directory)
    if collisions and not force:
        raise InitError(
            "Refusing to overwrite existing files: " + ", ".join(collisions),
            colliding=collisions,
        )

    written: list[Path] = []
    app_path = directory / "app.py"
    toml_path = directory / "pounce.toml"
    gitignore_path = directory / ".gitignore"

    app_path.write_text(APP_TEMPLATE, encoding="utf-8")
    written.append(app_path)

    toml_path.write_text(build_toml_template(), encoding="utf-8")
    written.append(toml_path)

    gitignore_path.write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
    written.append(gitignore_path)

    return written
