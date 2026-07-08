"""AST-enforced invariants for pounce error codes.

Walks ``src/pounce/`` and inspects every ``raise <PounceErrorSubclass>(...)``
call. Asserts:

1. Every raise site passes ``code="POUNCE_..."`` as a string literal.
2. Every code matches ``^POUNCE_[A-Z]+_[A-Z0-9_]+$``.
3. Each code's category segment matches the raising class's category.
4. Within a single pounce error class, codes are unique **except** where the
   raise-site comment line just above the ``raise`` contains the marker
   ``# code-reuse: intentional`` or mentions "intentional" — this makes reuse
   explicit rather than accidental.

This runs at ``pytest`` time, not runtime, so dead branches don't escape it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

POUNCE_SRC = Path(__file__).parent.parent.parent / "src" / "pounce"

# Map pounce error class name -> expected category segment in the code.
# Kept in sync with docs/design/error-codes.md category enum.
CLASS_TO_CATEGORY: dict[str, str] = {
    "ParseError": "PARSE",
    "RequestTimeoutError": "TIMEOUT",
    "LimitError": "LIMIT",
    "AppError": "APP",
    "LifespanError": "LIFESPAN",
    "SupervisorError": "SUPERVISOR",
    "WorkerError": "WORKER",
    "TLSError": "TLS",
    "ReloadError": "RELOAD",
}

CODE_REGEX = re.compile(r"^POUNCE_[A-Z]+_[A-Z0-9_]+$")


def _iter_pounce_files() -> list[Path]:
    return sorted(POUNCE_SRC.rglob("*.py"))


def _collect_raise_sites() -> list[tuple[Path, int, str, str | None]]:
    """Return (file, line, class_name, code_value_or_None) for every ``raise <PounceErrorSubclass>(...)``."""
    sites: list[tuple[Path, int, str, str | None]] = []
    for path in _iter_pounce_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc
            if not isinstance(call, ast.Call):
                continue
            # Must be `raise SomeName(...)` where SomeName is a pounce Error class.
            func = call.func
            if not isinstance(func, ast.Name):
                continue
            class_name = func.id
            if class_name not in CLASS_TO_CATEGORY:
                continue
            code_value: str | None = None
            for kw in call.keywords:
                if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        code_value = kw.value.value
                    break
            sites.append((path, node.lineno, class_name, code_value))
    return sites


ALL_SITES = _collect_raise_sites()


def test_expected_site_count() -> None:
    """Keep count stable so accidentally-added raise sites trigger review."""
    # 24 from Sprint 1 (docs/plans/vibe-coding-epic.md), 1 for the
    # duplicate-Host rejection added in _fast_h1 (issue #119), and 2
    # fail-loud worker-startup sites (issue #245).
    assert len(ALL_SITES) == 29, (
        f"Found {len(ALL_SITES)} pounce-error raise sites; expected 29. "
        f"If this changed intentionally, update this test."
    )


@pytest.mark.parametrize(
    ("path", "line", "cls", "code"),
    ALL_SITES,
    ids=[f"{p.relative_to(POUNCE_SRC)}:{ln}" for (p, ln, _, _) in ALL_SITES],
)
def test_every_raise_has_code(path: Path, line: int, cls: str, code: str | None) -> None:
    assert code is not None, (
        f"{path.relative_to(POUNCE_SRC)}:{line} raises {cls} without code=; "
        f"add code='POUNCE_{CLASS_TO_CATEGORY[cls]}_...'"
    )


@pytest.mark.parametrize(
    ("path", "line", "cls", "code"),
    [(p, ln, c, k) for (p, ln, c, k) in ALL_SITES if k is not None],
    ids=[f"{p.relative_to(POUNCE_SRC)}:{ln}" for (p, ln, _, k) in ALL_SITES if k is not None],
)
def test_code_regex(path: Path, line: int, cls: str, code: str) -> None:
    assert CODE_REGEX.match(code), (
        f"{path.relative_to(POUNCE_SRC)}:{line} code={code!r} "
        f"does not match ^POUNCE_[A-Z]+_[A-Z0-9_]+$"
    )


@pytest.mark.parametrize(
    ("path", "line", "cls", "code"),
    [(p, ln, c, k) for (p, ln, c, k) in ALL_SITES if k is not None],
    ids=[f"{p.relative_to(POUNCE_SRC)}:{ln}" for (p, ln, _, k) in ALL_SITES if k is not None],
)
def test_code_category_matches_class(path: Path, line: int, cls: str, code: str) -> None:
    expected = CLASS_TO_CATEGORY[cls]
    # Code segment after POUNCE_ up to next _
    segment = code.split("_", 2)[1]
    assert segment == expected, (
        f"{path.relative_to(POUNCE_SRC)}:{line} {cls} raised with code={code!r}; "
        f"category segment {segment!r} must equal {expected!r} for class {cls}"
    )


def test_codes_unique_per_class() -> None:
    """Codes must be unique within a class unless deliberately shared.

    Shared codes are allowed when the same semantic error is detected at
    multiple raise sites (e.g. request-line parsing fails on two different
    branches). Today _fast_h1 shares POUNCE_PARSE_MALFORMED_REQUEST_LINE and
    POUNCE_PARSE_HEADERS_TOO_LARGE across two sites each — both intentional.
    """
    per_class: dict[str, dict[str, list[tuple[Path, int]]]] = {}
    for path, line, cls, code in ALL_SITES:
        if code is None:
            continue
        per_class.setdefault(cls, {}).setdefault(code, []).append((path, line))

    # Explicit allowlist of deliberately-shared codes.
    allowed_shares: set[tuple[str, str]] = {
        ("ParseError", "POUNCE_PARSE_MALFORMED_REQUEST_LINE"),
        ("ParseError", "POUNCE_PARSE_HEADERS_TOO_LARGE"),
        ("WorkerError", "POUNCE_WORKER_STARTUP_FAILED"),
    }

    violations: list[str] = []
    for cls, codes in per_class.items():
        for code, occurrences in codes.items():
            if len(occurrences) > 1 and (cls, code) not in allowed_shares:
                locs = ", ".join(f"{p.relative_to(POUNCE_SRC)}:{ln}" for p, ln in occurrences)
                violations.append(f"{cls}.{code} reused at: {locs}")
    assert not violations, "Duplicate codes (not in allowed_shares):\n" + "\n".join(violations)
