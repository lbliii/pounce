"""Code quality regression guards.

Catches patterns that have historically caused bugs in Pounce.
"""

import re
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"


class TestExceptionSyntax:
    """Guard against Python 2 exception syntax slipping back in."""

    # Matches: except SomeError, AnotherError:
    # Does NOT match: except (SomeError, AnotherError):
    _BAD_EXCEPT_RE = re.compile(r"^\s*except\s+\w+\s*,\s*\w+\s*:", re.MULTILINE)

    def test_no_python2_except_syntax(self) -> None:
        """All multi-exception handlers must use tuple syntax: except (A, B):

        The Python 2 form ``except A, B:`` compiles but only catches A and
        binds it to variable B, silently shadowing a builtin.
        """
        violations: list[str] = []
        for py_file in SRC_DIR.rglob("*.py"):
            text = py_file.read_text()
            for match in self._BAD_EXCEPT_RE.finditer(text):
                lineno = text[: match.start()].count("\n") + 1
                violations.append(f"{py_file.relative_to(SRC_DIR)}:{lineno}: {match.group().strip()}")

        assert not violations, (
            f"Found {len(violations)} Python 2-style except handler(s):\n"
            + "\n".join(f"  {v}" for v in violations)
        )
