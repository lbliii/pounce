"""Code quality regression guards.

Catches patterns that have historically caused bugs in Pounce.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = _REPO_ROOT / "src"
EXAMPLES_DIR = _REPO_ROOT / "examples"

# Directories scanned for the banned parenless multi-except form. ``benchmarks``
# is intentionally omitted here (owned by a separate workstream); add it once its
# known violations are cleaned up.
_SCAN_DIRS = (SRC_DIR, EXAMPLES_DIR)


class TestExceptionSyntax:
    """Guard against the banned parenless multi-except form leaking in.

    On Python 3.14t+ (Pounce's target) ``except A, B:`` is **valid** PEP 758
    syntax that catches both ``A`` and ``B`` -- there is no silent shadowing.
    Pounce nonetheless bans it because it is a hard ``SyntaxError`` on every
    pre-3.14 reader and ``ruff format`` auto-introduces it whenever a
    parenthesized tuple lacks ``# fmt: skip``. Multi-type handlers must use the
    portable parenthesized tuple ``except (A, B):`` (with ``# fmt: skip`` to
    keep ruff from rewriting it) or be split into single-type / base-class
    clauses.
    """

    # Matches the parenless multi-except form, including:
    #   - bare names:    except A, B:
    #   - 3+ names:       except A, B, C:
    #   - dotted names:   except a.B, c.D:   /  except asyncio.CancelledError, OSError:
    # Does NOT match the allowed parenthesized tuple: except (A, B):
    # (the char after ``except `` there is ``(``, which is not in ``[\w.]``).
    _BAD_EXCEPT_RE = re.compile(r"^\s*except\s+[\w.]+(?:\s*,\s*[\w.]+)+\s*:", re.MULTILINE)

    def test_no_parenless_multi_except(self) -> None:
        """Every multi-type handler must use parenthesized tuple syntax."""
        violations: list[str] = []
        for scan_dir in _SCAN_DIRS:
            for py_file in scan_dir.rglob("*.py"):
                text = py_file.read_text()
                for match in self._BAD_EXCEPT_RE.finditer(text):
                    lineno = text[: match.start()].count("\n") + 1
                    violations.append(
                        f"{py_file.relative_to(_REPO_ROOT)}:{lineno}: {match.group().strip()}"
                    )

        assert not violations, (
            f"Found {len(violations)} parenless multi-except handler(s) "
            f"(use 'except (A, B):  # fmt: skip' or split clauses):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_regex_flags_bare_multi_except(self) -> None:
        """The two-bareword form must be flagged."""
        assert self._BAD_EXCEPT_RE.search("    except ValueError, TypeError:")

    def test_regex_flags_three_plus_names(self) -> None:
        """The 3+ name form must be flagged (the original regex missed it)."""
        assert self._BAD_EXCEPT_RE.search("    except OSError, ValueError, RuntimeError:")

    def test_regex_flags_dotted_names(self) -> None:
        r"""The dotted-name form must be flagged.

        This is the case that slipped past the original ``\w+`` regex (``\w``
        excludes the dot), e.g. the ``examples/streaming_sse.py`` violation
        that motivated the widening.
        """
        assert self._BAD_EXCEPT_RE.search(
            "    except asyncio.CancelledError, ConnectionError, OSError:"
        )
        assert self._BAD_EXCEPT_RE.search("    except a.B, c.D:")

    def test_regex_allows_parenthesized_tuple(self) -> None:
        """The portable parenthesized tuple form must NOT be flagged."""
        assert not self._BAD_EXCEPT_RE.search("    except (ValueError, TypeError):")
        assert not self._BAD_EXCEPT_RE.search(
            "    except (asyncio.CancelledError, ConnectionError, OSError):  # fmt: skip"
        )

    def test_regex_allows_single_except(self) -> None:
        """A single-type handler (dotted or not) must NOT be flagged."""
        assert not self._BAD_EXCEPT_RE.search("    except ValueError:")
        assert not self._BAD_EXCEPT_RE.search("    except asyncio.CancelledError:")
