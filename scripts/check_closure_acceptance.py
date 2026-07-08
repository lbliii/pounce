"""Require executable acceptance proof when a pull request closes an issue."""

# ruff: noqa: T201 - this CI command reports actionable results on stdout

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from issue_coverage import collect_issue_tests

_CLOSING = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#(\d+)",
    re.IGNORECASE,
)
_EXEMPT = re.compile(
    r"^\s*acceptance\s*:\s*(n/?a|none|not applicable)\b",
    re.IGNORECASE | re.MULTILINE,
)


def extract_closing_issues(body: str) -> set[int]:
    """Return issue numbers linked with a GitHub closing keyword."""
    return {int(match) for match in _CLOSING.findall(body or "")}


def is_exempt(body: str) -> bool:
    """Return whether the PR declares an explicit acceptance exemption."""
    return bool(_EXEMPT.search(body or ""))


def _read_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        return args.body
    if args.body_file is not None:
        return Path(args.body_file).read_text(encoding="utf-8")
    environment_body = os.environ.get("PR_BODY")
    if environment_body is not None:
        return environment_body
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body", help="PR body text")
    parser.add_argument("--body-file", help="path to a file containing the PR body")
    args = parser.parse_args(argv)
    body = _read_body(args)
    closing = extract_closing_issues(body)

    if not closing:
        print("No closing issue linkage in the PR body; nothing to gate.")
        return 0
    if is_exempt(body):
        print(
            "Explicit acceptance exemption recorded for "
            + ", ".join(f"#{number}" for number in sorted(closing))
        )
        return 0

    coverage = collect_issue_tests()
    missing = sorted(number for number in closing if number not in coverage)
    if missing:
        joined = ", ".join(f"#{number}" for number in missing)
        print(
            f"::error::This PR closes {joined} without an executable acceptance test.\n"
            "Add @pytest.mark.issue(N) to a test that proves the acceptance criteria, "
            "or add 'Acceptance: n/a (reason)' to the PR body for non-testable work.\n"
            "See docs/backlog-automation.md."
        )
        return 1

    print("Acceptance tests present for " + ", ".join(f"#{n}" for n in sorted(closing)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
