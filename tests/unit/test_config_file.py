"""Tests for TOML configuration file loading."""

from __future__ import annotations

import pytest

from pounce._config_file import (
    find_config_file,
    load_config_file,
    load_config_with_overrides,
)

# ---------------------------------------------------------------------------
# find_config_file
# ---------------------------------------------------------------------------


class TestFindConfigFile:
    def test_finds_pounce_toml(self, tmp_path):
        (tmp_path / "pounce.toml").write_text("")
        assert find_config_file(tmp_path) == tmp_path / "pounce.toml"

    def test_finds_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        assert find_config_file(tmp_path) == tmp_path / "pyproject.toml"

    def test_pounce_toml_takes_precedence(self, tmp_path):
        """pounce.toml wins over pyproject.toml."""
        (tmp_path / "pounce.toml").write_text("")
        (tmp_path / "pyproject.toml").write_text("")
        assert find_config_file(tmp_path) == tmp_path / "pounce.toml"

    def test_returns_none_when_no_file(self, tmp_path):
        assert find_config_file(tmp_path) is None


# ---------------------------------------------------------------------------
# load_config_file — pounce.toml
# ---------------------------------------------------------------------------


class TestLoadPounceToml:
    def test_basic_values(self, tmp_path):
        (tmp_path / "pounce.toml").write_text('host = "0.0.0.0"\nport = 9000\nworkers = 4\n')
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["host"] == "0.0.0.0"
        assert result["port"] == 9000
        assert result["workers"] == 4

    def test_boolean_values(self, tmp_path):
        (tmp_path / "pounce.toml").write_text(
            "reload = true\ncompression = false\naccess_log = false\n"
        )
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["reload"] is True
        assert result["compression"] is False
        assert result["access_log"] is False

    def test_float_values(self, tmp_path):
        (tmp_path / "pounce.toml").write_text("keep_alive_timeout = 30.0\nrequest_timeout = 60.0\n")
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["keep_alive_timeout"] == 30.0
        assert result["request_timeout"] == 60.0

    def test_static_files_table(self, tmp_path):
        (tmp_path / "pounce.toml").write_text(
            '[static_files]\n"/static" = "./public"\n"/assets" = "./dist"\n'
        )
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["static_files"] == {"/static": "./public", "/assets": "./dist"}

    def test_trusted_hosts_list(self, tmp_path):
        (tmp_path / "pounce.toml").write_text('trusted_hosts = ["127.0.0.1", "10.0.0.1"]\n')
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["trusted_hosts"] == frozenset({"127.0.0.1", "10.0.0.1"})

    def test_reload_include_list(self, tmp_path):
        (tmp_path / "pounce.toml").write_text('reload_include = [".html", ".css"]\n')
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["reload_include"] == (".html", ".css")

    def test_reload_dirs_list(self, tmp_path):
        (tmp_path / "pounce.toml").write_text('reload_dirs = ["src", "templates"]\n')
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["reload_dirs"] == ("src", "templates")

    def test_unknown_key_raises(self, tmp_path):
        (tmp_path / "pounce.toml").write_text('bogus_key = "oops"\n')
        with pytest.raises(ValueError, match=r"Unknown keys.*bogus_key"):
            load_config_file(tmp_path / "pounce.toml")

    def test_empty_file(self, tmp_path):
        (tmp_path / "pounce.toml").write_text("")
        result = load_config_file(tmp_path / "pounce.toml")
        assert result == {}


# ---------------------------------------------------------------------------
# load_config_file — pyproject.toml [tool.pounce]
# ---------------------------------------------------------------------------


class TestLoadPyprojectToml:
    def test_reads_tool_pounce_section(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.pounce]\nhost = "0.0.0.0"\nport = 9000\n')
        result = load_config_file(tmp_path / "pyproject.toml")
        assert result["host"] == "0.0.0.0"
        assert result["port"] == 9000

    def test_ignores_other_tool_sections(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 100\n\n[tool.pounce]\nport = 3000\n"
        )
        result = load_config_file(tmp_path / "pyproject.toml")
        assert result == {"port": 3000}

    def test_missing_tool_pounce_section(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        result = load_config_file(tmp_path / "pyproject.toml")
        assert result == {}

    def test_empty_tool_pounce_section(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pounce]\n")
        result = load_config_file(tmp_path / "pyproject.toml")
        assert result == {}

    def test_unknown_key_in_pyproject_raises(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pounce]\nnope = true\n")
        with pytest.raises(ValueError, match=r"Unknown keys.*nope"):
            load_config_file(tmp_path / "pyproject.toml")


# ---------------------------------------------------------------------------
# load_config_with_overrides — merging
# ---------------------------------------------------------------------------


class TestLoadConfigWithOverrides:
    def test_cli_overrides_file(self, tmp_path):
        (tmp_path / "pounce.toml").write_text("port = 9000\nworkers = 8\n")
        result = load_config_with_overrides(
            {"port": 3000, "workers": None},
            search_dir=tmp_path,
        )
        # CLI port wins, TOML workers fills in
        assert result["port"] == 3000
        assert result["workers"] == 8

    def test_none_cli_values_dont_override(self, tmp_path):
        (tmp_path / "pounce.toml").write_text('host = "0.0.0.0"\n')
        result = load_config_with_overrides(
            {"host": None},
            search_dir=tmp_path,
        )
        assert result["host"] == "0.0.0.0"

    def test_no_config_file_uses_cli_only(self, tmp_path):
        result = load_config_with_overrides(
            {"port": 5000, "host": "localhost"},
            search_dir=tmp_path,
        )
        assert result == {"port": 5000, "host": "localhost"}

    def test_explicit_config_path(self, tmp_path):
        custom = tmp_path / "custom.toml"
        custom.write_text("port = 7777\n")
        # Rename so it's not auto-detected as pounce.toml
        renamed = tmp_path / "my-config.toml"
        custom.rename(renamed)

        result = load_config_with_overrides({}, config_path=renamed)
        assert result["port"] == 7777

    def test_explicit_path_skips_search(self, tmp_path):
        """When config_path is given, pounce.toml in cwd is ignored."""
        (tmp_path / "pounce.toml").write_text("port = 1111\n")
        custom = tmp_path / "other.toml"
        custom.write_text("port = 2222\n")

        result = load_config_with_overrides({}, config_path=custom)
        assert result["port"] == 2222

    def test_invalid_toml_raises(self, tmp_path):
        import tomllib

        (tmp_path / "pounce.toml").write_text("not valid toml [[[")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_config_with_overrides({}, search_dir=tmp_path)

    def test_file_values_create_valid_serverconfig(self, tmp_path):
        """Values from TOML can create a valid ServerConfig."""
        from pounce.config import ServerConfig

        (tmp_path / "pounce.toml").write_text(
            'host = "0.0.0.0"\nport = 9000\nworkers = 2\nlog_level = "debug"\n'
        )
        result = load_config_with_overrides({}, search_dir=tmp_path)
        config = ServerConfig(**result)
        assert config.host == "0.0.0.0"
        assert config.port == 9000
        assert config.workers == 2
        assert config.log_level == "debug"


# ---------------------------------------------------------------------------
# Deprecated alias: reload_dir → reload_dirs
# ---------------------------------------------------------------------------


class TestDeprecatedAliases:
    def test_reload_dir_alias_accepted(self, tmp_path):
        """reload_dir in TOML is accepted as alias for reload_dirs."""
        (tmp_path / "pounce.toml").write_text('reload_dir = ["src", "templates"]\n')
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["reload_dirs"] == ("src", "templates")
        assert "reload_dir" not in result

    def test_reload_dir_alias_emits_warning(self, tmp_path, caplog):
        """reload_dir alias emits a deprecation warning."""
        import logging

        (tmp_path / "pounce.toml").write_text('reload_dir = ["src"]\n')
        with caplog.at_level(logging.WARNING, logger="pounce.config"):
            load_config_file(tmp_path / "pounce.toml")
        assert "deprecated" in caplog.text.lower()
        assert "reload_dirs" in caplog.text

    def test_both_reload_dir_and_reload_dirs_raises(self, tmp_path):
        """Having both reload_dir and reload_dirs raises ValueError."""
        (tmp_path / "pounce.toml").write_text('reload_dir = ["src"]\nreload_dirs = ["lib"]\n')
        with pytest.raises(ValueError, match=r"Both.*reload_dir.*reload_dirs"):
            load_config_file(tmp_path / "pounce.toml")

    def test_reload_dirs_canonical_still_works(self, tmp_path):
        """The canonical reload_dirs name still works without warning."""
        (tmp_path / "pounce.toml").write_text('reload_dirs = ["src"]\n')
        result = load_config_file(tmp_path / "pounce.toml")
        assert result["reload_dirs"] == ("src",)
