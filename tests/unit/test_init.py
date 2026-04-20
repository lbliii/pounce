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
