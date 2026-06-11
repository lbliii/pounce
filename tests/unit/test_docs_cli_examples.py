"""Guard: every documented ``pounce`` CLI invocation must arg-parse.

Issue #143 — documented examples used a non-existent positional-app form
(``pounce myapp:app``). The canonical form is ``pounce serve --app myapp:app``.
This test extracts every ``pounce ...`` shell invocation from README.md and the
site docs and asserts the real CLI parser accepts it, so the examples cannot
silently rot back to a broken form.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from pounce._cli import cli

REPO_ROOT = Path(__file__).parent.parent.parent

# Doc trees whose fenced shell blocks are user-facing copy/paste invocations.
DOC_GLOBS: tuple[tuple[Path, str], ...] = (
    (REPO_ROOT, "README.md"),
    (REPO_ROOT / "site" / "content", "**/*.md"),
)

# Subcommands the parser knows about; an invocation must start with one of
# these (or be a bare ``pounce --flag`` global invocation, which we skip).
KNOWN_SUBCOMMANDS = {"bench", "serve", "info", "check", "init", "config"}

# Tokens that mean "this is illustrative output / shell scaffolding", not a
# literal CLI invocation we should hand to argparse.
_SKIP_SUBSTRINGS = (
    "$(",  # command substitution
    "$",  # shell variable interpolation (e.g. Railway's --port "$PORT")
    "&&",
    "|",
    "#",  # trailing inline comment makes shlex ambiguous
    "[OPTIONS]",  # usage-syntax placeholder, not a literal invocation
    "APP [",  # usage-syntax line (``pounce serve --app APP [OPTIONS]``)
)


def _strip_exec_start(line: str) -> str:
    """Reduce a systemd ``ExecStart=/path/to/pounce ...`` line to ``pounce ...``."""
    if "ExecStart=" in line:
        line = line.split("ExecStart=", 1)[1]
        # Drop the absolute path prefix on the binary, keep the args.
        parts = line.split(None, 1)
        binary = Path(parts[0]).name
        rest = parts[1] if len(parts) > 1 else ""
        return f"{binary} {rest}".strip()
    return line


def _iter_doc_files() -> list[Path]:
    files: list[Path] = []
    for base, pattern in DOC_GLOBS:
        if pattern.endswith(".md") and "*" not in pattern:
            files.append(base / pattern)
        else:
            files.extend(sorted(base.glob(pattern)))
    return [f for f in files if f.is_file()]


def _extract_pounce_invocations() -> list[tuple[str, int, str]]:
    """Return (file, line_no, command) for every literal ``pounce`` invocation."""
    invocations: list[tuple[str, int, str]] = []
    fence_re = re.compile(r"^```")
    for path in _iter_doc_files():
        in_fence = False
        # Track multi-line invocations that use trailing backslash continuation.
        pending: list[str] = []
        pending_lineno = 0
        for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
            if fence_re.match(raw):
                in_fence = not in_fence
                continue
            if not in_fence:
                continue
            line = _strip_exec_start(raw.strip())
            if pending:
                pending.append(line.rstrip("\\").strip())
                if not line.endswith("\\"):
                    invocations.append(
                        (str(path.relative_to(REPO_ROOT)), pending_lineno, " ".join(pending))
                    )
                    pending = []
                continue
            if not line.startswith("pounce "):
                continue
            if any(tok in line for tok in _SKIP_SUBSTRINGS):
                continue
            if line.endswith("\\"):
                pending = [line.rstrip("\\").strip()]
                pending_lineno = lineno
                continue
            invocations.append((str(path.relative_to(REPO_ROOT)), lineno, line))
    return invocations


_INVOCATIONS = _extract_pounce_invocations()


def test_docs_contain_pounce_invocations() -> None:
    """Sanity check: the extractor actually found documented invocations."""
    assert len(_INVOCATIONS) >= 20, _INVOCATIONS


def test_no_positional_app_invocation_in_docs() -> None:
    """No doc may use the broken ``pounce <subcommand> <app>`` positional form."""
    offenders: list[str] = []
    for path, lineno, cmd in _INVOCATIONS:
        tokens = shlex.split(cmd)
        # tokens[0] == "pounce"; a serve invocation must pass --app, never a
        # bare positional app reference like ``pounce serve myapp:app``.
        if len(tokens) >= 2 and tokens[1] == "serve" and "--app" not in tokens:
            offenders.append(f"{path}:{lineno}: {cmd}")
        # ``pounce myapp:app`` (no subcommand) is also broken.
        if (
            len(tokens) >= 2
            and tokens[1] not in KNOWN_SUBCOMMANDS
            and tokens[1].startswith("-") is False
        ):
            offenders.append(f"{path}:{lineno}: {cmd}")
    assert not offenders, "Broken positional-app invocations found:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("path", "lineno", "command"),
    _INVOCATIONS,
    ids=[f"{p}:{n}" for p, n, _ in _INVOCATIONS],
)
def test_documented_invocation_parses(path: str, lineno: int, command: str) -> None:
    """Every documented ``pounce`` invocation must be accepted by the parser."""
    parser = cli.build_parser()
    tokens = shlex.split(command)
    assert tokens[0] == "pounce", command
    args = tokens[1:]
    # Bare global invocations (``pounce --version``, ``pounce init``) and other
    # subcommands are fine; argparse raises SystemExit on a bad invocation.
    try:
        parser.parse_args(args)
    except SystemExit as exc:
        # ``--help`` / ``--version`` exit cleanly with code 0; that is a
        # successful parse, not a doc error. Anything else is a real failure.
        if exc.code not in (0, None):  # pragma: no cover - failure path
            pytest.fail(f"{path}:{lineno}: `{command}` failed to parse (exit={exc.code})")


_SHELL_FENCE_LANGS = {"bash", "sh", "shell", "console", ""}
_FENCE_LANG_RE = re.compile(r"^```(\w*)")


def _split_command_offenders() -> list[str]:
    """Find ``pounce`` commands whose flags were split onto an orphaned line.

    Regression guard for issue #143: a complete ``pounce ...`` line that does
    not end with a ``\\`` continuation must not be immediately followed by a
    flag-only line. (The arg-parse test above misses this because it parses the
    truncated first line, which is itself valid, and skips the orphaned flags.)
    """
    offenders: list[str] = []
    for path in _iter_doc_files():
        lines = path.read_text().splitlines()
        in_fence = False
        lang = ""
        for i, raw in enumerate(lines):
            m = _FENCE_LANG_RE.match(raw)
            if m:
                in_fence = not in_fence
                lang = m.group(1) if in_fence else ""
                continue
            if not in_fence or lang not in _SHELL_FENCE_LANGS:
                continue
            stripped = raw.strip()
            if not stripped.startswith("pounce ") or stripped.endswith("\\"):
                continue
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and not _FENCE_LANG_RE.match(lines[j]):
                nxt = lines[j].strip()
                if nxt.startswith("-"):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{i + 1}: `{stripped}` "
                        f"followed by orphaned flags `{nxt}`"
                    )
    return offenders


def test_no_split_pounce_commands_in_docs() -> None:
    """A ``pounce`` command's flags must stay on its line or use ``\\`` (issue #143)."""
    offenders = _split_command_offenders()
    assert not offenders, "Split/orphaned-flag commands found:\n" + "\n".join(offenders)
