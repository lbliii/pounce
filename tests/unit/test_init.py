"""Unit tests for :mod:`pounce._init`."""

from __future__ import annotations

from pathlib import Path

import pytest

from pounce._init import (
    APP_TEMPLATE,
    GITIGNORE_TEMPLATE,
    SCAFFOLD_FILES,
    InitError,
    run_init,
)


class TestRunInit:
    def test_writes_all_three_files(self, tmp_path: Path) -> None:
        written = run_init(tmp_path)
        names = {p.name for p in written}
        assert names == set(SCAFFOLD_FILES)
        for name in SCAFFOLD_FILES:
            assert (tmp_path / name).exists()

    def test_app_py_is_valid_python(self, tmp_path: Path) -> None:
        run_init(tmp_path)
        content = (tmp_path / "app.py").read_text(encoding="utf-8")
        # Compilable — a non-trivial correctness check.
        compile(content, str(tmp_path / "app.py"), "exec")
        assert "async def app" in content

    def test_app_py_matches_template(self, tmp_path: Path) -> None:
        run_init(tmp_path)
        assert (tmp_path / "app.py").read_text(encoding="utf-8") == APP_TEMPLATE

    def test_gitignore_matches_template(self, tmp_path: Path) -> None:
        run_init(tmp_path)
        assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == GITIGNORE_TEMPLATE

    def test_pounce_toml_has_commented_defaults(self, tmp_path: Path) -> None:
        run_init(tmp_path)
        content = (tmp_path / "pounce.toml").read_text(encoding="utf-8")
        # pounce.toml uses top-level keys, not a [pounce] section header.
        assert "[pounce]" not in content
        # Every field is commented out — users uncomment what they need.
        assert "# port =" in content

    def test_refuses_collision_without_force(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("existing", encoding="utf-8")
        with pytest.raises(InitError) as ei:
            run_init(tmp_path)
        assert "app.py" in ei.value.colliding
        # Original file untouched
        assert (tmp_path / "app.py").read_text(encoding="utf-8") == "existing"

    def test_lists_all_colliding_files(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x", encoding="utf-8")
        (tmp_path / "pounce.toml").write_text("x", encoding="utf-8")
        with pytest.raises(InitError) as ei:
            run_init(tmp_path)
        assert set(ei.value.colliding) == {"app.py", "pounce.toml"}

    def test_force_overwrites(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("old", encoding="utf-8")
        run_init(tmp_path, force=True)
        # Overwritten with the template.
        assert (tmp_path / "app.py").read_text(encoding="utf-8") == APP_TEMPLATE

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        nope = tmp_path / "does-not-exist"
        with pytest.raises(InitError, match="does not exist"):
            run_init(nope)

    def test_not_a_directory_raises(self, tmp_path: Path) -> None:
        regular = tmp_path / "a-file"
        regular.write_text("x", encoding="utf-8")
        with pytest.raises(InitError, match="Not a directory"):
            run_init(regular)


class TestAppTemplateSignposts:
    """Sprint 4: ``app.py`` docstring is a tour guide, not a one-liner.

    A fresh agent reading the scaffolded file should be able to find every
    other command pounce ships without leaving the file. The template's
    leading docstring names the commands it expects the user to reach for
    next and points at the troubleshooting catalog. These assertions fence
    the template against accidental regression — every signpost must stay.
    """

    REQUIRED_SIGNPOSTS = (
        "pounce serve",
        "pounce check",
        "pounce config schema",
        "pounce config show",
        "pounce info",
        "pounce --mcp",
        "docs/troubleshooting.md",
    )

    def test_generated_app_py_is_valid_python(self, tmp_path: Path) -> None:
        # Every signpost lives inside a Python docstring; the file must still
        # parse. This defends against unescaped triple quotes or stray
        # brackets in the signpost block.
        run_init(tmp_path)
        src = (tmp_path / "app.py").read_text(encoding="utf-8")
        compile(src, str(tmp_path / "app.py"), "exec")

    def test_every_signpost_present_in_generated_file(self, tmp_path: Path) -> None:
        run_init(tmp_path)
        src = (tmp_path / "app.py").read_text(encoding="utf-8")
        missing = [s for s in self.REQUIRED_SIGNPOSTS if s not in src]
        assert not missing, (
            "Generated app.py is missing required signposts — a future agent "
            "would have to leave the file to find these:\n  " + "\n  ".join(missing)
        )

    def test_signposts_live_inside_module_docstring(self, tmp_path: Path) -> None:
        # If someone moves the signposts out of the docstring (e.g. into a
        # print statement) they stop being a table-of-contents for readers of
        # the file. Assert they're in the module docstring.
        import ast

        run_init(tmp_path)
        src = (tmp_path / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        docstring = ast.get_docstring(tree) or ""
        missing = [s for s in self.REQUIRED_SIGNPOSTS if s not in docstring]
        assert not missing, (
            "Signposts must live inside the module docstring so they render "
            "in help(app), IDE hovers, and plain reading. Missing from "
            "docstring:\n  " + "\n  ".join(missing)
        )

    def test_docstring_stays_tight(self, tmp_path: Path) -> None:
        # The plan budgets ≤15 non-blank lines for the docstring: this is
        # signposts, not a tutorial. Cap keeps the file approachable.
        import ast

        run_init(tmp_path)
        src = (tmp_path / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        docstring = ast.get_docstring(tree) or ""
        non_blank = [ln for ln in docstring.splitlines() if ln.strip()]
        assert len(non_blank) <= 15, (
            f"Docstring has {len(non_blank)} non-blank lines; budget is 15. "
            "If you're adding a new command, consider removing a less-common "
            f"one first:\n{docstring}"
        )

    def test_response_body_unchanged(self, tmp_path: Path) -> None:
        # The rest of the generated app — the behaviour users will actually
        # exercise — must be byte-identical to what Sprint 4 replaced. Parse
        # the generated module and assert the ASGI send() returns the
        # original payload.
        run_init(tmp_path)
        src = (tmp_path / "app.py").read_text(encoding="utf-8")
        # The response body is a bytes literal in the second send() call.
        # Matching on the literal is a cheap proxy for "behaviour unchanged"
        # without having to spin up a full ASGI harness.
        assert b"hello from pounce" in src.encode("utf-8")


class TestGeneratedPounceTomlLoads:
    """The generated pounce.toml must be loadable by the real config loader."""

    def test_generated_toml_loads_without_error(self, tmp_path: Path) -> None:
        from pounce._config_file import load_config_file

        run_init(tmp_path)
        loaded = load_config_file(tmp_path / "pounce.toml")
        # Every field is commented out — so the loaded dict is empty.
        assert loaded == {}

    def test_generated_toml_round_trips_through_serverconfig(self, tmp_path: Path) -> None:
        from pounce._config_file import load_config_with_overrides
        from pounce.config import ServerConfig

        run_init(tmp_path)
        merged = load_config_with_overrides({}, config_path=tmp_path / "pounce.toml")
        # Constructing ServerConfig must succeed — no unknown fields, no bad types.
        ServerConfig(**merged)
