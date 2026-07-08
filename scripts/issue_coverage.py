"""Map ``@pytest.mark.issue(N)`` markers to acceptance tests."""

# ruff: noqa: T201 - this command-line report is intentionally printed

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_TEST_ROOTS = ("tests", "benchmarks")


def _issue_args_from_decorator(node: ast.expr) -> list[int]:
    """Return literal issue numbers from a pytest issue-marker decorator."""
    if not isinstance(node, ast.Call):
        return []
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "issue"):
        return []
    return [
        arg.value
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, int)
    ]


def _module_level_issue_markers(tree: ast.Module) -> list[int]:
    issues: list[int] = []
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets
        ):
            continue
        value = node.value
        marks: Iterable[ast.expr]
        if isinstance(value, (ast.List, ast.Tuple)):
            marks = value.elts
        elif value is not None:
            marks = [value]
        else:
            marks = []
        for mark in marks:
            issues.extend(_issue_args_from_decorator(mark))
    return issues


def _collect_from_body(
    body: list[ast.stmt],
    stack: list[str],
    inherited: set[int],
    relative_path: str,
    record: Callable[[int, str], None],
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            own = {
                number
                for mark in node.decorator_list
                for number in _issue_args_from_decorator(mark)
            }
            qualified_name = "::".join([*stack, node.name])
            for issue in own | inherited:
                record(issue, f"{relative_path}::{qualified_name}")
        elif isinstance(node, ast.ClassDef):
            class_issues = {
                number
                for mark in node.decorator_list
                for number in _issue_args_from_decorator(mark)
            }
            _collect_from_body(
                node.body,
                [*stack, node.name],
                inherited | class_issues,
                relative_path,
                record,
            )


def collect_issue_tests(roots: Iterable[Path] | None = None) -> dict[int, list[str]]:
    """Return issue numbers mapped to the tests carrying their markers."""
    if roots is None:
        roots = [_REPO_ROOT / root for root in _DEFAULT_TEST_ROOTS]
    mapping: dict[int, set[str]] = {}

    def record(issue: int, location: str) -> None:
        mapping.setdefault(issue, set()).add(location)

    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("test_*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except OSError, SyntaxError:
                continue
            try:
                relative_path = path.relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                relative_path = path.name

            for issue in _module_level_issue_markers(tree):
                record(issue, f"{relative_path}::<module>")
            _collect_from_body(tree.body, [], set(), relative_path, record)

    return {issue: sorted(locations) for issue, locations in sorted(mapping.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", type=int, help="show tests proving one issue")
    parser.add_argument("--json", action="store_true", help="emit the complete map as JSON")
    parser.add_argument(
        "--untested",
        type=int,
        nargs="+",
        metavar="N",
        help="exit nonzero if any listed issue lacks an acceptance test",
    )
    args = parser.parse_args(argv)
    coverage = collect_issue_tests()

    if args.untested is not None:
        missing = [number for number in args.untested if number not in coverage]
        if missing:
            print(
                "Issues without a @pytest.mark.issue acceptance test: "
                + ", ".join(f"#{number}" for number in missing)
            )
            return 1
        print("All listed issues have at least one acceptance test.")
        return 0
    if args.json:
        print(json.dumps(coverage, indent=2))
        return 0
    if args.issue is not None:
        locations = coverage.get(args.issue, [])
        if not locations:
            print(f"#{args.issue}: no acceptance test found.")
            return 0
        print(f"#{args.issue}: {len(locations)} acceptance test(s)")
        for location in locations:
            print(f"  {location}")
        return 0

    for issue, locations in coverage.items():
        print(f"#{issue}: {len(locations)} acceptance test(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
