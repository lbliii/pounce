"""Tests for the file watcher / reload module."""

import tempfile
import time
from pathlib import Path

from pounce._reload import (
    _EXCLUDE_DIRS,
    _WATCH_EXTENSIONS,
    _should_watch,
    _snapshot,
    detect_changes,
)


class TestShouldWatch:
    def test_python_file(self) -> None:
        assert _should_watch(Path("src/app.py")) is True

    def test_yaml_file(self) -> None:
        assert _should_watch(Path("config.yaml")) is True

    def test_toml_file(self) -> None:
        assert _should_watch(Path("pyproject.toml")) is True

    def test_image_file_excluded(self) -> None:
        assert _should_watch(Path("logo.png")) is False

    def test_pycache_excluded(self) -> None:
        assert _should_watch(Path("__pycache__/app.cpython-314.pyc")) is False

    def test_git_excluded(self) -> None:
        assert _should_watch(Path(".git/config")) is False

    def test_node_modules_excluded(self) -> None:
        assert _should_watch(Path("node_modules/package/index.js")) is False

    def test_venv_excluded(self) -> None:
        assert _should_watch(Path(".venv/lib/python3.14/site.py")) is False


class TestSnapshot:
    def test_snapshot_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _snapshot([Path(tmpdir)])
            assert result == {}

    def test_snapshot_with_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "app.py").write_text("# hello")
            (p / "config.yaml").write_text("key: val")
            (p / "data.txt").write_text("not watched")

            result = _snapshot([p])
            # Should include .py and .yaml but not .txt
            assert str(p / "app.py") in result
            assert str(p / "config.yaml") in result
            assert str(p / "data.txt") not in result

    def test_snapshot_excludes_pycache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            cache = p / "__pycache__"
            cache.mkdir()
            (cache / "app.cpython-314.pyc").write_bytes(b"\x00")

            result = _snapshot([p])
            assert len(result) == 0


class TestDetectChanges:
    def test_new_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            snapshot = _snapshot([p])

            # Create a new file
            (p / "new.py").write_text("# new file")

            changed, _ = detect_changes([p], snapshot)
            assert str(p / "new.py") in changed

    def test_modified_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            f = p / "app.py"
            f.write_text("# v1")

            snapshot = _snapshot([p])

            # Modify the file (ensure mtime changes)
            time.sleep(0.05)
            f.write_text("# v2")

            changed, _ = detect_changes([p], snapshot)
            assert str(f) in changed

    def test_deleted_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            f = p / "app.py"
            f.write_text("# temp")

            snapshot = _snapshot([p])

            # Delete the file
            f.unlink()

            changed, _ = detect_changes([p], snapshot)
            assert str(f) in changed

    def test_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "app.py").write_text("# stable")

            snapshot = _snapshot([p])
            changed, _ = detect_changes([p], snapshot)
            assert len(changed) == 0


class TestExcludeDirs:
    def test_common_excludes(self) -> None:
        assert "__pycache__" in _EXCLUDE_DIRS
        assert ".git" in _EXCLUDE_DIRS
        assert "node_modules" in _EXCLUDE_DIRS
        assert ".venv" in _EXCLUDE_DIRS
        assert "venv" in _EXCLUDE_DIRS


class TestExtraExtensions:
    """Extra extensions are merged with the built-in set."""

    def test_html_not_watched_by_default(self) -> None:
        assert _should_watch(Path("index.html")) is False

    def test_html_watched_with_extra(self) -> None:
        extra = _WATCH_EXTENSIONS | frozenset({".html"})
        assert _should_watch(Path("index.html"), extra) is True

    def test_css_watched_with_extra(self) -> None:
        extra = _WATCH_EXTENSIONS | frozenset({".css"})
        assert _should_watch(Path("style.css"), extra) is True

    def test_extra_extensions_merged_in_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "app.py").write_text("# code")
            (p / "index.html").write_text("<h1>Hi</h1>")
            (p / "style.css").write_text("body {}")

            # Default: only .py
            default = _snapshot([p])
            assert str(p / "app.py") in default
            assert str(p / "index.html") not in default

            # With extras: .py + .html + .css
            ext = _WATCH_EXTENSIONS | frozenset({".html", ".css"})
            extended = _snapshot([p], ext)
            assert str(p / "app.py") in extended
            assert str(p / "index.html") in extended
            assert str(p / "style.css") in extended

    def test_extra_extensions_in_detect_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            ext = _WATCH_EXTENSIONS | frozenset({".html"})

            snapshot = _snapshot([p], ext)

            # Create an HTML file
            (p / "page.html").write_text("<p>Hello</p>")

            changed, _ = detect_changes([p], snapshot, ext)
            assert str(p / "page.html") in changed
