"""Tests for branded traceback rendering."""

from pounce._output import _hint_for_crash, _shorten_path, branded_traceback


class TestShortenPath:
    """_shorten_path abbreviates file paths for display."""

    def test_site_packages(self):
        result = _shorten_path("/usr/lib/python3.14/site-packages/starlette/routing.py")
        assert result == "starlette/routing.py"

    def test_home_dir(self):
        import os

        home = os.path.expanduser("~")
        result = _shorten_path(f"{home}/projects/myapp/main.py")
        assert result == "~/projects/myapp/main.py"

    def test_no_shortening_needed(self):
        result = _shorten_path("main.py")
        assert result == "main.py"


class TestHintForCrash:
    """_hint_for_crash returns helpful hints for common exceptions."""

    def test_key_error_state(self):
        exc = KeyError("state")
        assert "lifespan" in _hint_for_crash(exc).lower()

    def test_import_error(self):
        exc = ImportError("No module named 'redis'")
        assert "module" in _hint_for_crash(exc).lower()

    def test_memory_error(self):
        exc = MemoryError()
        assert "memory" in _hint_for_crash(exc).lower()

    def test_permission_error(self):
        exc = PermissionError("Permission denied")
        assert "permission" in _hint_for_crash(exc).lower()

    def test_connection_refused(self):
        exc = ConnectionRefusedError("Connection refused")
        assert "backend" in _hint_for_crash(exc).lower() or "unreachable" in _hint_for_crash(exc).lower()

    def test_encoding_error(self):
        exc = UnicodeDecodeError("utf-8", b"", 0, 1, "invalid start byte")
        assert "encoding" in _hint_for_crash(exc).lower()

    def test_generic_error_no_hint(self):
        exc = RuntimeError("something went wrong")
        assert _hint_for_crash(exc) == ""


class TestBrandedTraceback:
    """branded_traceback renders crash reports."""

    def test_renders_without_error(self, mocker):
        mock_write = mocker.patch("pounce._output._write")
        mocker.patch("pounce._output._is_pretty", return_value=True)

        try:
            raise ValueError("test error")
        except ValueError as exc:
            branded_traceback(exc)

        mock_write.assert_called_once()
        output = mock_write.call_args[0][0]
        assert "crash" in output
        assert "ValueError" in output
        assert "test error" in output

    def test_renders_with_worker_id(self, mocker):
        mock_write = mocker.patch("pounce._output._write")
        mocker.patch("pounce._output._is_pretty", return_value=True)

        try:
            raise RuntimeError("worker crash")
        except RuntimeError as exc:
            branded_traceback(exc, worker_id=3)

        output = mock_write.call_args[0][0]
        assert "3" in output

    def test_plain_text_fallback(self, mocker):
        mock_logger = mocker.patch("pounce._output.logger")
        mocker.patch("pounce._output._is_pretty", return_value=False)
        mocker.patch("pounce._output.sys.stderr.isatty", return_value=False)
        mocker.patch.dict("os.environ", {}, clear=True)

        try:
            raise TypeError("bad type")
        except TypeError as exc:
            branded_traceback(exc)

        mock_logger.error.assert_called()
        first_call = mock_logger.error.call_args_list[0]
        assert "TypeError" in str(first_call) or "bad type" in str(first_call)

    def test_no_traceback(self, mocker):
        """Exception without __traceback__ still renders."""
        mock_write = mocker.patch("pounce._output._write")
        mocker.patch("pounce._output._is_pretty", return_value=True)

        exc = RuntimeError("no tb")
        exc.__traceback__ = None
        branded_traceback(exc)

        mock_write.assert_called_once()
        output = mock_write.call_args[0][0]
        assert "crash" in output


class TestTracebackTemplate:
    """The traceback.kida template renders without errors."""

    def test_template_renders(self):
        from pounce._output import _render

        result = _render(
            "traceback.kida",
            exc_type="ValueError",
            exc_message="invalid literal for int()",
            frames=[
                {"filename": "myapp/views.py", "lineno": 42, "name": "handler", "line": "int('abc')", "is_last": True},
            ],
            worker_id=1,
            hint="Check input validation.",
        )
        assert "crash" in result
        assert "ValueError" in result
        assert "myapp/views.py" in result
        assert "42" in result

    def test_template_renders_no_hint_no_worker(self):
        from pounce._output import _render

        result = _render(
            "traceback.kida",
            exc_type="RuntimeError",
            exc_message="oops",
            frames=[
                {"filename": "main.py", "lineno": 10, "name": "run", "line": "", "is_last": True},
            ],
            worker_id=None,
            hint="",
        )
        assert "crash" in result
        assert "RuntimeError" in result

    def test_template_multiple_frames(self):
        from pounce._output import _render

        result = _render(
            "traceback.kida",
            exc_type="KeyError",
            exc_message="'db'",
            frames=[
                {"filename": "lib/db.py", "lineno": 5, "name": "connect", "line": "pool[key]", "is_last": False},
                {"filename": "app.py", "lineno": 20, "name": "startup", "line": "db.connect()", "is_last": True},
            ],
            worker_id=None,
            hint="",
        )
        assert "lib/db.py" in result
        assert "app.py" in result
