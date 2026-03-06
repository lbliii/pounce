"""Tests for pounce._importer — application string resolution and reimport."""

import sys

import pytest

from pounce._importer import _clear_local_modules, import_app, reimport_app


class TestImportApp:
    """import_app() resolves 'module:attribute' to a callable."""

    def test_import_stdlib_callable(self):
        # json.dumps is a callable in the stdlib
        result = import_app("json:dumps")
        import json

        assert result is json.dumps

    def test_import_dotted_module(self):
        # os.path.join is accessible via os.path:join
        result = import_app("os.path:join")
        import os.path

        assert result is os.path.join

    def test_import_nested_attribute(self):
        # os:path.join — attribute path with dots
        result = import_app("os:path.join")
        import os.path

        assert result is os.path.join


class TestImportAppErrors:
    """import_app() raises clear errors for invalid inputs."""

    def test_no_colon_raises_value_error(self):
        with pytest.raises(ValueError, match="Expected format"):
            import_app("myapp")

    def test_empty_module_raises_value_error(self):
        with pytest.raises(ValueError, match="Module name is empty"):
            import_app(":app")

    def test_empty_attribute_raises_value_error(self):
        with pytest.raises(ValueError, match="Attribute name is empty"):
            import_app("myapp:")

    def test_missing_module_raises_import_error(self):
        with pytest.raises(ImportError, match="Could not import"):
            import_app("nonexistent_module_xyz:app")

    def test_missing_attribute_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="has no attribute"):
            import_app("json:nonexistent_attr_xyz")

    def test_non_callable_raises_type_error(self):
        # json.decoder is a module attribute but __name__ is a string
        with pytest.raises(TypeError, match="is not callable"):
            import_app("json:__name__")


class TestFactoryPattern:
    """import_app() supports 'module:factory()' syntax."""

    def test_factory_call(self, tmp_path, monkeypatch):
        """Factory pattern calls the attribute and returns its result."""
        import sys

        # Create a temporary module with a factory function
        mod_file = tmp_path / "fakefactory.py"
        mod_file.write_text(
            "async def create_app():\n    pass\n\ndef make_app():\n    return create_app\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        # Ensure fresh import
        sys.modules.pop("fakefactory", None)

        result = import_app("fakefactory:make_app()")
        # The factory was called, returning create_app
        import fakefactory

        assert result is fakefactory.create_app

    def test_empty_factory_name_raises(self):
        with pytest.raises(ValueError, match="Attribute name is empty"):
            import_app("json:()")

    def test_non_callable_factory_raises(self):
        with pytest.raises(TypeError, match="is not callable"):
            import_app("json:__name__()")


# ------------------------------------------------------------------
# _clear_local_modules
# ------------------------------------------------------------------


class TestClearLocalModules:
    """_clear_local_modules() evicts project-local entries from sys.modules."""

    @staticmethod
    def _import_fresh(name: str) -> None:
        """Import a module from scratch, ensuring no stale cache."""
        import importlib

        sys.modules.pop(name, None)
        importlib.import_module(name)

    def test_clears_module_under_base_dir(self, tmp_path, monkeypatch):
        """Modules whose __file__ is under base_dirs get removed."""
        mod_file = tmp_path / "clearme.py"
        mod_file.write_text("VALUE = 1\n")
        monkeypatch.syspath_prepend(str(tmp_path))

        self._import_fresh("clearme")
        assert "clearme" in sys.modules

        removed = _clear_local_modules(base_dirs=[str(tmp_path)])
        assert "clearme" in removed
        assert "clearme" not in sys.modules

    def test_ignores_stdlib_modules(self):
        """Standard library modules are never cleared."""
        removed = _clear_local_modules(base_dirs=["/nonexistent"])
        assert "json" not in removed
        assert "json" in sys.modules

    def test_ignores_modules_outside_base_dirs(self, tmp_path, monkeypatch):
        """Modules outside every base_dir are left untouched."""
        mod_file = tmp_path / "keepme.py"
        mod_file.write_text("VALUE = 1\n")
        monkeypatch.syspath_prepend(str(tmp_path))

        self._import_fresh("keepme")
        assert "keepme" in sys.modules

        # Use a directory that doesn't contain tmp_path
        removed = _clear_local_modules(base_dirs=["/completely/different/path"])
        assert "keepme" not in removed
        assert "keepme" in sys.modules

        # cleanup
        sys.modules.pop("keepme", None)

    def test_no_false_match_on_directory_prefix(self, tmp_path, monkeypatch):
        """'/app' must not match modules under '/application'."""
        subdir = tmp_path / "application"
        subdir.mkdir()
        mod_file = subdir / "mymod.py"
        mod_file.write_text("VALUE = 1\n")
        monkeypatch.syspath_prepend(str(subdir))

        self._import_fresh("mymod")
        assert "mymod" in sys.modules

        # Use a base_dir that is a prefix of the subdir name but different
        fake_base = tmp_path / "app"
        fake_base.mkdir(exist_ok=True)
        removed = _clear_local_modules(base_dirs=[str(fake_base)])
        assert "mymod" not in removed
        assert "mymod" in sys.modules

        # cleanup
        sys.modules.pop("mymod", None)


# ------------------------------------------------------------------
# reimport_app
# ------------------------------------------------------------------


class TestReimportApp:
    """reimport_app() picks up code changes from disk."""

    def test_reimport_picks_up_changes(self, tmp_path, monkeypatch):
        """After modifying source on disk, reimport returns the new value."""
        mod_file = tmp_path / "hotmod.py"
        mod_file.write_text("async def app(scope, receive, send): return 'v1'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("hotmod", None)

        app_v1 = import_app("hotmod:app")

        # Modify source on disk
        mod_file.write_text("async def app(scope, receive, send): return 'v2'\n")

        app_v2 = reimport_app("hotmod:app", base_dirs=[str(tmp_path)])

        # The two app references must be different objects
        assert app_v1 is not app_v2

        # cleanup
        sys.modules.pop("hotmod", None)

    def test_reimport_factory_picks_up_changes(self, tmp_path, monkeypatch):
        """Factory pattern also gets a fresh result after reimport."""
        mod_file = tmp_path / "hotfactory.py"
        mod_file.write_text(
            "GENERATION = 1\n"
            "async def _app(scope, receive, send): pass\n"
            "def create_app():\n"
            "    _app.generation = GENERATION\n"
            "    return _app\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("hotfactory", None)

        app_v1 = reimport_app("hotfactory:create_app()", base_dirs=[str(tmp_path)])
        assert app_v1.generation == 1  # type: ignore[attr-defined]

        # Bump generation on disk
        mod_file.write_text(
            "GENERATION = 2\n"
            "async def _app(scope, receive, send): pass\n"
            "def create_app():\n"
            "    _app.generation = GENERATION\n"
            "    return _app\n"
        )

        app_v2 = reimport_app("hotfactory:create_app()", base_dirs=[str(tmp_path)])
        assert app_v2.generation == 2  # type: ignore[attr-defined]

        # cleanup
        sys.modules.pop("hotfactory", None)

    def test_reimport_with_syntax_error_raises(self, tmp_path, monkeypatch):
        """If the new source has a syntax error, reimport raises cleanly."""
        mod_file = tmp_path / "badmod.py"
        mod_file.write_text("async def app(scope, receive, send): pass\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("badmod", None)

        # First import succeeds
        import_app("badmod:app")

        # Introduce a syntax error
        mod_file.write_text("def app( = broken\n")

        # SyntaxError propagates through ImportError on re-import
        with pytest.raises((SyntaxError, ImportError)):
            reimport_app("badmod:app", base_dirs=[str(tmp_path)])

        # sys.modules should not contain the broken module
        assert "badmod" not in sys.modules

    def test_reimport_clears_submodules(self, tmp_path, monkeypatch):
        """Reimport also clears submodules of a package."""
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "routes.py").write_text("VALUE = 'old'\n")
        (pkg_dir / "app.py").write_text(
            "from mypkg.routes import VALUE\nasync def app(scope, receive, send): return VALUE\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        for name in list(sys.modules):
            if name.startswith("mypkg"):
                del sys.modules[name]

        app_v1 = reimport_app("mypkg.app:app", base_dirs=[str(tmp_path)])

        # Modify the dependency
        (pkg_dir / "routes.py").write_text("VALUE = 'new'\n")

        app_v2 = reimport_app("mypkg.app:app", base_dirs=[str(tmp_path)])

        # The app was reimported with fresh submodules
        assert app_v1 is not app_v2

        # Verify the submodule was actually reloaded
        import mypkg.routes

        assert mypkg.routes.VALUE == "new"

        # cleanup
        for name in list(sys.modules):
            if name.startswith("mypkg"):
                del sys.modules[name]
