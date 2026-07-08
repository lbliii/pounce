"""Ratchet actionable static raise messages on public-path code.

This ports zoomies' raise-message gate without pretending Pounce's existing
diagnostics already satisfy it. Statically recoverable messages in public
functions must end with punctuation and contain at least ``MIN_WORDS`` words.
Private helper functions, bare re-raises, and dynamic message variables are
outside this syntactic check; PounceError code/catalog coverage is enforced by
the existing error-code and troubleshooting tests.

The committed baseline fingerprints every current violation. CI fails when a
new or changed violation appears *or* when fixed debt is not removed from the
baseline. Regenerate deliberately with ``--write-baseline PATH`` after
reviewing the reported message changes.
"""

# ruff: noqa: T201 - this CI command reports actionable results on stdout/stderr

from __future__ import annotations

import argparse
import ast
import hashlib
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

MIN_WORDS = 8
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "pounce"
DEFAULT_BASELINE = REPO_ROOT / "docs" / "design" / "raise-message-baseline.txt"


@dataclass(frozen=True, slots=True)
class Violation:
    """One deterministic lint finding plus its human-readable location."""

    fingerprint: str
    path: str
    line: int
    scope: str
    exception: str
    rule: str
    message: str

    def render(self) -> str:
        return (
            f"{self.path}:{self.line}: {self.rule} in {self.scope} "
            f"({self.exception}) — {self.message!r}"
        )


def _extract_static_text(node: ast.AST) -> str | None:
    """Reconstruct the statically visible portion of a message expression."""
    match node:
        case ast.Constant(value=str() as value):
            return value
        case ast.JoinedStr(values=values):
            parts: list[str] = []
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                else:
                    parts.append(" X ")
            return "".join(parts)
        case ast.BinOp(op=ast.Add()):
            left = _extract_static_text(node.left)
            right = _extract_static_text(node.right)
            if left is None and right is None:
                return None
            return (left or "") + (right or "")
        case _:
            return None


def _exception_name(node: ast.Call) -> str:
    match node.func:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
        case _:
            return "<dynamic>"


def _is_private_scope(scope_stack: list[str]) -> bool:
    """Return whether the innermost function is private but not a dunder."""
    for name in reversed(scope_stack):
        if name.startswith("class "):
            continue
        if name.startswith("__") and name.endswith("__"):
            return False
        return name.startswith("_")
    return False


def _stable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _fingerprint(*, path: str, scope: str, exception: str, rule: str, message: str) -> str:
    identity = "\0".join((path, scope, exception, rule, message))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def check_file(path: Path) -> list[Violation]:
    """Return actionable-message violations found in one Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    stable_path = _stable_path(path)
    violations: list[Violation] = []
    scope_stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _visit_scope(self, name: str, node: ast.AST) -> None:
            scope_stack.append(name)
            self.generic_visit(node)
            scope_stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_scope(f"class {node.name}", node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_scope(node.name, node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_scope(node.name, node)

        def visit_Raise(self, node: ast.Raise) -> None:
            self.generic_visit(node)
            if _is_private_scope(scope_stack) or node.exc is None:
                return
            if not isinstance(node.exc, ast.Call) or not node.exc.args:
                return
            message = _extract_static_text(node.exc.args[0])
            if message is None:
                return

            stripped = message.strip()
            rules: list[str] = []
            if not stripped:
                rules.append("message is empty")
            else:
                if not stripped.endswith((".", "?")):
                    rules.append("message must end with '.' or '?'")
                word_count = len(stripped.split())
                if word_count < MIN_WORDS:
                    rules.append(f"message has {word_count} words; need at least {MIN_WORDS}")

            scope = ".".join(scope_stack) or "<module>"
            exception = _exception_name(node.exc)
            for rule in rules:
                fingerprint = _fingerprint(
                    path=stable_path,
                    scope=scope,
                    exception=exception,
                    rule=rule,
                    message=stripped,
                )
                violations.append(
                    Violation(
                        fingerprint=fingerprint,
                        path=stable_path,
                        line=node.lineno,
                        scope=scope,
                        exception=exception,
                        rule=rule,
                        message=stripped,
                    )
                )

    Visitor().visit(tree)
    return violations


def collect_violations(paths: list[Path]) -> list[Violation]:
    """Collect findings from every requested Python file."""
    violations: list[Violation] = []
    for path in paths:
        violations.extend(check_file(path))
    return violations


def _load_baseline(path: Path) -> Counter[str]:
    fingerprints = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return Counter(fingerprints)


def _write_baseline(path: Path, violations: list[Violation]) -> None:
    fingerprints = sorted(violation.fingerprint for violation in violations)
    content = [
        "# Pounce raise-message debt baseline. See scripts/lint_raise_messages.py.",
        f"# Findings: {len(fingerprints)}. Remove fingerprints only with reviewed fixes.",
        *fingerprints,
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def _report_drift(violations: list[Violation], baseline: Counter[str]) -> int:
    current = Counter(violation.fingerprint for violation in violations)
    added = current - baseline
    resolved = baseline - current
    if not added and not resolved:
        print(f"Raise-message baseline clean: {sum(current.values())} known finding(s).")
        return 0

    by_fingerprint: dict[str, list[Violation]] = defaultdict(list)
    for violation in violations:
        by_fingerprint[violation.fingerprint].append(violation)

    if added:
        print("New or changed raise-message violations:", file=sys.stderr)
        remaining = added.copy()
        for fingerprint in sorted(remaining):
            for violation in by_fingerprint[fingerprint][: remaining[fingerprint]]:
                print(f"  {violation.render()}", file=sys.stderr)
    if resolved:
        print(
            f"{sum(resolved.values())} baseline finding(s) were resolved; "
            "remove their fingerprints from the baseline.",
            file=sys.stderr,
        )
    print(
        "Review the changes, then regenerate the baseline deliberately with "
        "--write-baseline docs/design/raise-message-baseline.txt.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Python files to check directly")
    parser.add_argument("--baseline", type=Path, help="override the committed baseline path")
    parser.add_argument("--write-baseline", type=Path, help="write current fingerprints here")
    args = parser.parse_args(argv)

    explicit_paths = bool(args.paths)
    paths = args.paths or sorted(SRC_ROOT.rglob("*.py"))
    violations = collect_violations(paths)

    if args.write_baseline is not None:
        _write_baseline(args.write_baseline, violations)
        print(f"Wrote {len(violations)} finding(s) to {args.write_baseline}.")
        return 0

    baseline_path = args.baseline or (None if explicit_paths else DEFAULT_BASELINE)
    if baseline_path is None:
        if not violations:
            print(f"Raise-message check clean: {len(paths)} file(s) scanned.")
            return 0
        print("Raise-message convention violations:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation.render()}", file=sys.stderr)
        return 1
    if not baseline_path.exists():
        print(f"Raise-message baseline not found: {baseline_path}", file=sys.stderr)
        return 1
    return _report_drift(violations, _load_baseline(baseline_path))


if __name__ == "__main__":
    raise SystemExit(main())
