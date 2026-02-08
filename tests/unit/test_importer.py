"""Tests for pounce._importer — application string resolution."""

import pytest

from pounce._importer import import_app


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
