"""Tests for pounce._runtime — GIL detection and worker mode selection."""

from unittest.mock import patch

import pytest

from pounce._errors import SupervisorError
from pounce._runtime import (
    default_worker_count,
    detect_worker_mode,
    is_gil_enabled,
    resolve_worker_execution_mode,
    resolve_worker_model,
    validate_subinterpreter_app_path,
)


class TestIsGilEnabled:
    """is_gil_enabled() wraps sys._is_gil_enabled with safe fallback."""

    def test_returns_bool(self):
        result = is_gil_enabled()
        assert isinstance(result, bool)

    def test_nogil_when_disabled(self):
        with patch("pounce._runtime.sys") as mock_sys:
            mock_sys._is_gil_enabled = lambda: False
            assert is_gil_enabled() is False

    def test_gil_when_enabled(self):
        with patch("pounce._runtime.sys") as mock_sys:
            mock_sys._is_gil_enabled = lambda: True
            assert is_gil_enabled() is True

    def test_fallback_when_no_attribute(self):
        """Python < 3.13 doesn't have sys._is_gil_enabled."""
        with patch("pounce._runtime.sys", spec=[]):
            # No _is_gil_enabled attribute — should default to True (GIL)
            result = is_gil_enabled()
            assert result is True


class TestDetectWorkerMode:
    """detect_worker_mode() chooses threads vs processes based on GIL."""

    def test_thread_on_nogil(self):
        with patch("pounce._runtime.is_gil_enabled", return_value=False):
            assert detect_worker_mode() == "thread"

    def test_process_on_gil(self):
        with patch("pounce._runtime.is_gil_enabled", return_value=True):
            assert detect_worker_mode() == "process"


class TestDefaultWorkerCount:
    """default_worker_count() returns a sensible CPU-based default."""

    def test_returns_positive_int(self):
        count = default_worker_count()
        assert isinstance(count, int)
        assert count >= 1

    def test_uses_cpu_count(self):
        with patch("pounce._runtime.os.cpu_count", return_value=8):
            assert default_worker_count() == 8

    def test_fallback_when_cpu_count_none(self):
        with patch("pounce._runtime.os.cpu_count", return_value=None):
            assert default_worker_count() == 1


class TestResolveWorkerExecutionMode:
    """resolve_worker_execution_mode() maps config to sync/async."""

    def test_sync_explicit(self):
        assert resolve_worker_execution_mode("sync") == "sync"

    def test_async_explicit(self):
        assert resolve_worker_execution_mode("async") == "async"

    def test_auto_nogil_uses_sync(self):
        with patch("pounce._runtime.is_gil_enabled", return_value=False):
            assert resolve_worker_execution_mode("auto") == "sync"

    def test_auto_gil_uses_async(self):
        with patch("pounce._runtime.is_gil_enabled", return_value=True):
            assert resolve_worker_execution_mode("auto") == "async"


class TestResolveWorkerModel:
    """The observable model matches the server's actual worker path (#246)."""

    @pytest.mark.issue(246)
    def test_auto_nogil_is_thread_sync(self):
        with patch("pounce._runtime.is_gil_enabled", return_value=False):
            assert resolve_worker_model("auto", 4) == "thread (sync)"

    @pytest.mark.issue(246)
    def test_auto_gil_is_process_async(self):
        with patch("pounce._runtime.is_gil_enabled", return_value=True):
            assert resolve_worker_model("auto", 4) == "process (async)"

    def test_single_worker_uses_direct_async_path(self):
        assert resolve_worker_model("auto", 1) == "single (async)"

    def test_subinterpreter_is_explicit_even_with_one_worker(self):
        assert resolve_worker_model("subinterpreter", 1) == "subinterpreter (async)"

    @pytest.mark.issue(246)
    def test_missing_subinterpreter_app_path_has_actionable_code(self):
        with pytest.raises(SupervisorError) as caught:
            validate_subinterpreter_app_path("subinterpreter", None)

        assert caught.value.code == "POUNCE_SUPERVISOR_SUBINTERPRETER_NO_APP_PATH"
        assert "app_path" in (caught.value.hint or "")
