"""Tests for display resolution and DisplayConfig."""

import io
import json
import os
from pathlib import Path
from typing import ClassVar
from unittest import mock

import pytest

from pounce.config import ServerConfig
from pounce.display import DisplayConfig, resolve_display_config


class TestDisplayConfig:
    def test_defaults(self) -> None:
        d = DisplayConfig()
        assert d.name is None
        assert d.tagline is None
        assert d.version is None
        assert d.lines == ()
        assert d.signage is None

    def test_invalid_signage_raises(self) -> None:
        with pytest.raises(ValueError, match="signage"):
            DisplayConfig(signage="bogus")  # type: ignore[arg-type]


class TestResolveDisplayConfig:
    def test_config_only(self) -> None:
        r = resolve_display_config(
            config_display=DisplayConfig(
                name="App",
                tagline="Tag",
                version="1.0.0",
                lines=("extra",),
                signage="minimal",
            ),
        )
        assert r.name == "App"
        assert r.tagline == "Tag"
        assert r.version == "1.0.0"
        assert r.lines == ("extra",)
        assert r.signage == "minimal"

    def test_cli_overrides_config(self) -> None:
        r = resolve_display_config(
            cli_name="CLI",
            config_display=DisplayConfig(name="Cfg", tagline="T", version="2", signage="full"),
        )
        assert r.name == "CLI"
        assert r.tagline == "T"
        assert r.version == "2"

    def test_env_overrides_config_not_cli(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"POUNCE_APP_NAME": "Env", "POUNCE_APP_VERSION": "3"},
            clear=False,
        ):
            r = resolve_display_config(
                config_display=DisplayConfig(name="Cfg", version="0"),
            )
        assert r.name == "Env"
        assert r.version == "3"

    def test_cli_overrides_env(self) -> None:
        with mock.patch.dict(os.environ, {"POUNCE_APP_NAME": "Env"}, clear=False):
            r = resolve_display_config(
                cli_name="CLI",
                config_display=None,
            )
        assert r.name == "CLI"

    def test_config_signage_full_beats_app_minimal(self) -> None:
        class App:
            __pounce_display__: ClassVar[dict[str, str]] = {"signage": "minimal"}

        r = resolve_display_config(
            config_display=DisplayConfig(signage="full"),
            app=App(),
        )
        assert r.signage == "full"

    def test_app_hook_dict_lowest_priority(self) -> None:
        class App:
            __pounce_display__: ClassVar[dict[str, str]] = {
                "name": "Hook",
                "version": "9.9.9",
            }

        r = resolve_display_config(
            config_display=DisplayConfig(name="Cfg"),
            app=App(),
        )
        assert r.name == "Cfg"
        assert r.version == "9.9.9"

    def test_app_hook_callable(self) -> None:
        class App:
            @staticmethod
            def __pounce_display__():
                return {"name": "Called", "signage": "minimal"}

        r = resolve_display_config(app=App())
        assert r.name == "Called"
        assert r.signage == "minimal"

    def test_app_hook_invalid_ignored(self) -> None:
        class App:
            __pounce_display__: ClassVar[str] = "not-a-dict"

        r = resolve_display_config(
            cli_name="X",
            app=App(),
        )
        assert r.name == "X"

    def test_pyproject_discovery(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for key in (
            "POUNCE_APP_NAME",
            "POUNCE_APP_TAGLINE",
            "POUNCE_APP_VERSION",
            "POUNCE_SIGNAGE",
            "POUNCE_APP_PYPROJECT",
        ):
            monkeypatch.delenv(key, raising=False)
        proj = tmp_path / "pyproject.toml"
        proj.write_text(
            '[tool.pounce.display]\nname = "FromToml"\ntagline = "Hi"\n',
            encoding="utf-8",
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)
        r = resolve_display_config()
        assert r.name == "FromToml"
        assert r.tagline == "Hi"

    def test_pyproject_explicit_path_overrides_walk(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for key in (
            "POUNCE_APP_NAME",
            "POUNCE_APP_TAGLINE",
            "POUNCE_APP_VERSION",
            "POUNCE_SIGNAGE",
            "POUNCE_APP_PYPROJECT",
        ):
            monkeypatch.delenv(key, raising=False)
        a = tmp_path / "a.toml"
        a.write_text('[tool.pounce.display]\nname = "A"\n', encoding="utf-8")
        b = tmp_path / "b.toml"
        b.write_text('[tool.pounce.display]\nname = "B"\n', encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        r = resolve_display_config(pyproject_path=str(b))
        assert r.name == "B"

    def test_empty_name_omits_app_identity_fields(self) -> None:
        r = resolve_display_config(
            config_display=DisplayConfig(name=None, tagline="orphan"),
        )
        assert r.name is None


class TestStartupJsonApp:
    def test_json_banner_includes_app_when_name_set(self) -> None:
        import pounce.logging as pl
        from pounce._runtime import WorkerMode
        from pounce.display import DisplayConfig
        from pounce.server import Server

        async def app(scope, receive, send) -> None:
            pass

        cfg = ServerConfig(
            log_format="json",
            port=8765,
            host="127.0.0.1",
            display=DisplayConfig(name="MyApp", tagline="T", version="1.2.3"),
        )
        srv = Server(cfg, app)
        buf = io.StringIO()
        old = pl._resolved_format
        try:
            pl._resolved_format = "json"
            with mock.patch("sys.stderr", buf):
                srv._print_banner(1, WorkerMode.THREAD)
        finally:
            pl._resolved_format = old

        data = json.loads(buf.getvalue().strip())
        assert data["event"] == "startup"
        assert data["app"] == {"name": "MyApp", "tagline": "T", "version": "1.2.3"}
