"""Tests for pounce._cpu_affinity — worker CPU pinning (Linux)."""

from unittest.mock import MagicMock, patch

from pounce._cpu_affinity import maybe_pin_worker
from pounce.config import ServerConfig


def test_maybe_pin_worker_no_op_when_disabled() -> None:
    """When cpu_affinity=False, does nothing (no sched_setaffinity call)."""
    config = ServerConfig(cpu_affinity=False)
    with patch("pounce._cpu_affinity.sys") as mock_sys:
        mock_sys.platform = "linux"
        with patch("pounce._cpu_affinity.os") as mock_os:
            mock_os.cpu_count.return_value = 8
            mock_os.sched_setaffinity = MagicMock()
            maybe_pin_worker(0, config)
            mock_os.sched_setaffinity.assert_not_called()


def test_maybe_pin_worker_no_op_on_non_linux() -> None:
    """When not Linux, does nothing."""
    config = ServerConfig(cpu_affinity=True)
    with patch("pounce._cpu_affinity.sys") as mock_sys:
        mock_sys.platform = "darwin"
        with patch("pounce._cpu_affinity.os") as mock_os:
            mock_os.sched_setaffinity = MagicMock()
            maybe_pin_worker(0, config)
            mock_os.sched_setaffinity.assert_not_called()


def test_maybe_pin_worker_calls_sched_setaffinity_on_linux() -> None:
    """On Linux with cpu_affinity=True, calls sched_setaffinity."""
    config = ServerConfig(cpu_affinity=True)
    with patch("pounce._cpu_affinity.sys") as mock_sys:
        mock_sys.platform = "linux"
        with patch("pounce._cpu_affinity.os") as mock_os:
            mock_os.cpu_count.return_value = 8
            mock_os.sched_setaffinity = MagicMock()
            maybe_pin_worker(3, config)
            mock_os.sched_setaffinity.assert_called_once_with(0, {3})


def test_maybe_pin_worker_wraps_core_with_modulo() -> None:
    """Worker ID wraps to core via modulo when workers > cpu_count."""
    config = ServerConfig(cpu_affinity=True)
    with patch("pounce._cpu_affinity.sys") as mock_sys:
        mock_sys.platform = "linux"
        with patch("pounce._cpu_affinity.os") as mock_os:
            mock_os.cpu_count.return_value = 4
            mock_os.sched_setaffinity = MagicMock()
            maybe_pin_worker(5, config)  # 5 % 4 = 1
            mock_os.sched_setaffinity.assert_called_once_with(0, {1})


def test_maybe_pin_worker_handles_oserror_gracefully() -> None:
    """OSError (e.g. restricted cpuset) is caught and does not propagate."""
    config = ServerConfig(cpu_affinity=True)
    with patch("pounce._cpu_affinity.sys") as mock_sys:
        mock_sys.platform = "linux"
        with patch("pounce._cpu_affinity.os") as mock_os:
            mock_os.cpu_count.return_value = 8
            mock_os.sched_setaffinity = MagicMock(side_effect=OSError(22, "Invalid argument"))
            maybe_pin_worker(0, config)  # Should not raise
