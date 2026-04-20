"""Coverage test for ``docs/troubleshooting.md``.

Every ``POUNCE_*`` code that pounce emits at runtime must have a catalog
entry. The sources are:

1. ``raise <PounceError>(code="POUNCE_...")`` sites anywhere in
   ``src/pounce/`` — AST-walked.
2. ``self._send_error(code="POUNCE_...")`` calls in the protocol layer, which
   emit codes without raising. AST-walked.
3. ``default_code`` class attributes on each ``PounceError`` subclass — the
   fallback codes emitted when a raise site omits an explicit ``code=``.

Each collected code must appear as a ``### POUNCE_...`` markdown heading in
``docs/troubleshooting.md``. If you add a new code, add an entry there too.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
POUNCE_SRC = REPO_ROOT / "src" / "pounce"
CATALOG = REPO_ROOT / "docs" / "troubleshooting.md"

CODE_REGEX = re.compile(r"^POUNCE_[A-Z]+_[A-Z0-9_]+$")
HEADING_REGEX = re.compile(r"^###\s+(POUNCE_[A-Z0-9_]+)\s*$", re.MULTILINE)


def _collect_emitted_codes() -> set[str]:
    """All POUNCE_* codes that can be produced at runtime."""
    codes: set[str] = set()
    for path in sorted(POUNCE_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            # raise <Name>(..., code="POUNCE_...")
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                codes.update(_codes_from_keywords(node.exc.keywords))
            # <x>._send_error(..., code="POUNCE_...")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "_send_error":
                    codes.update(_codes_from_keywords(node.keywords))
            # class Foo(PounceError): default_code: str = "POUNCE_..."
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "default_code"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and CODE_REGEX.match(node.value.value)
            ):
                codes.add(node.value.value)
    return codes


def _codes_from_keywords(keywords: list[ast.keyword]) -> set[str]:
    out: set[str] = set()
    for kw in keywords:
        if kw.arg != "code":
            continue
        if (
            isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
            and CODE_REGEX.match(kw.value.value)
        ):
            out.add(kw.value.value)
    return out


def _collect_catalog_headings() -> set[str]:
    text = CATALOG.read_text(encoding="utf-8")
    return set(HEADING_REGEX.findall(text))


def test_every_emitted_code_has_catalog_entry() -> None:
    emitted = _collect_emitted_codes()
    documented = _collect_catalog_headings()
    missing = sorted(emitted - documented)
    assert not missing, (
        "The following POUNCE_* codes are emitted but missing from "
        "docs/troubleshooting.md. Add a `### <code>` heading for each:\n  "
        + "\n  ".join(missing)
    )


def test_no_stale_catalog_entries() -> None:
    emitted = _collect_emitted_codes()
    documented = _collect_catalog_headings()
    stale = sorted(documented - emitted)
    assert not stale, (
        "The following POUNCE_* codes are documented in "
        "docs/troubleshooting.md but no longer emitted anywhere in "
        "src/pounce/. Remove the stale entries:\n  " + "\n  ".join(stale)
    )
