"""Tests for the file watcher / reload module."""

import tempfile
import threading
import time
from pathlib import Path

from pounce._reload import (
    _EXCLUDE_DIRS,
    _WATCH_EXTENSIONS,
    _should_watch,
    _snapshot,
    detect_changes,
    watch_for_changes,
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


class TestDefaultWatchSet:
    """The built-in watch set covers static-site authoring files (issue #132)."""

    def test_python_watched_by_default(self) -> None:
        # .py watching must remain intact.
        assert _should_watch(Path("app.py")) is True

    def test_static_authoring_extensions_in_default_set(self) -> None:
        # Content/asset authoring files reload out of the box under --reload.
        for ext in (".md", ".html", ".css", ".js", ".svg"):
            assert ext in _WATCH_EXTENSIONS, ext

    def test_html_watched_by_default(self) -> None:
        assert _should_watch(Path("index.html")) is True

    def test_markdown_watched_by_default(self) -> None:
        assert _should_watch(Path("content/post.md")) is True

    def test_css_js_svg_watched_by_default(self) -> None:
        assert _should_watch(Path("style.css")) is True
        assert _should_watch(Path("app.js")) is True
        assert _should_watch(Path("logo.svg")) is True

    def test_unwatched_asset_still_excluded(self) -> None:
        # Binary assets we never want to poll on remain out of the set.
        assert _should_watch(Path("logo.png")) is False
        assert _should_watch(Path("data.txt")) is False

    def test_static_files_in_default_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "app.py").write_text("# code")
            (p / "index.html").write_text("<h1>Hi</h1>")
            (p / "style.css").write_text("body {}")
            (p / "post.md").write_text("# Hi")
            (p / "logo.png").write_bytes(b"\x89PNG")

            default = _snapshot([p])
            assert str(p / "app.py") in default
            assert str(p / "index.html") in default
            assert str(p / "style.css") in default
            assert str(p / "post.md") in default
            assert str(p / "logo.png") not in default


class TestExtraExtensions:
    """Extra extensions are merged with the built-in set."""

    def test_extra_extension_watched(self) -> None:
        extra = _WATCH_EXTENSIONS | frozenset({".rst"})
        assert _should_watch(Path("doc.rst"), extra) is True

    def test_extra_extensions_merged_in_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "app.py").write_text("# code")
            (p / "doc.rst").write_text("Title")

            # Default: .rst not watched.
            default = _snapshot([p])
            assert str(p / "app.py") in default
            assert str(p / "doc.rst") not in default

            # With extras: .py + .rst
            ext = _WATCH_EXTENSIONS | frozenset({".rst"})
            extended = _snapshot([p], ext)
            assert str(p / "app.py") in extended
            assert str(p / "doc.rst") in extended

    def test_extra_extensions_in_detect_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            ext = _WATCH_EXTENSIONS | frozenset({".html"})

            snapshot = _snapshot([p], ext)

            # Create an HTML file
            (p / "page.html").write_text("<p>Hello</p>")

            changed, _ = detect_changes([p], snapshot, ext)
            assert str(p / "page.html") in changed


class TestWatchForChanges:
    """Integration tests for the watch_for_changes polling loop."""

    def test_callback_fires_on_change(self) -> None:
        """Callback is invoked when a file changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "app.py").write_text("# v1")

            called = threading.Event()

            def on_change():
                called.set()

            stop = threading.Event()
            t = threading.Thread(
                target=watch_for_changes,
                args=([p], on_change),
                kwargs={"interval": 0.1, "stop_event": stop},
                daemon=True,
            )
            t.start()

            try:
                # Modify file after watcher has taken initial snapshot
                time.sleep(0.15)
                (p / "app.py").write_text("# v2")

                assert called.wait(timeout=2.0), "callback was not called"
            finally:
                stop.set()
                t.join(timeout=2.0)

    def test_stops_on_stop_event(self) -> None:
        """Watcher exits cleanly when stop_event is set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            stop = threading.Event()

            t = threading.Thread(
                target=watch_for_changes,
                args=([p], lambda: None),
                kwargs={"interval": 0.1, "stop_event": stop},
                daemon=True,
            )
            t.start()
            stop.set()
            t.join(timeout=2.0)
            assert not t.is_alive()

    def test_extra_extensions_triggers_callback(self) -> None:
        """Callback fires for files matching extra_extensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)

            called = threading.Event()
            stop = threading.Event()

            t = threading.Thread(
                target=watch_for_changes,
                args=([p], called.set),
                kwargs={
                    "interval": 0.1,
                    "stop_event": stop,
                    "extra_extensions": (".html",),
                },
                daemon=True,
            )
            t.start()

            try:
                time.sleep(0.15)
                (p / "index.html").write_text("<h1>Hi</h1>")

                assert called.wait(timeout=2.0), "callback was not called for .html"
            finally:
                stop.set()
                t.join(timeout=2.0)

    def test_empty_extra_extensions_uses_defaults(self) -> None:
        """Empty extra_extensions still watches default extensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)

            called = threading.Event()
            stop = threading.Event()

            t = threading.Thread(
                target=watch_for_changes,
                args=([p], called.set),
                kwargs={
                    "interval": 0.1,
                    "stop_event": stop,
                    "extra_extensions": (),
                },
                daemon=True,
            )
            t.start()

            try:
                time.sleep(0.15)
                (p / "app.py").write_text("# new")

                assert called.wait(timeout=2.0), "callback was not called for .py"
            finally:
                stop.set()
                t.join(timeout=2.0)


class TestReloadWatchDirs:
    """``Server._reload_watch_dirs`` includes static mount dirs (issue #132)."""

    @staticmethod
    async def _app(scope, receive, send):  # pragma: no cover - never invoked
        raise AssertionError

    def _server(self, **config_kwargs):
        from pounce.config import ServerConfig
        from pounce.server import Server

        return Server(ServerConfig(**config_kwargs), self._app)

    def test_includes_cwd(self) -> None:
        server = self._server()
        dirs = server._reload_watch_dirs()
        assert Path.cwd() in dirs

    def test_includes_reload_dirs(self, tmp_path: Path) -> None:
        extra = tmp_path / "templates"
        extra.mkdir()
        server = self._server(reload_dirs=(str(extra),))
        assert extra.resolve() in server._reload_watch_dirs()

    def test_includes_static_mount_dirs(self, tmp_path: Path) -> None:
        # Static assets served from OUTSIDE cwd must still be watched so that
        # editing them triggers a reload (issue #132).
        assets = tmp_path / "site_assets"
        assets.mkdir()
        server = self._server(static_files={"/static": str(assets)})
        assert assets.resolve() in server._reload_watch_dirs()

    def test_deduplicates(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        server = self._server(
            reload_dirs=(str(shared),),
            static_files={"/static": str(shared)},
        )
        dirs = server._reload_watch_dirs()
        assert dirs.count(shared.resolve()) == 1
