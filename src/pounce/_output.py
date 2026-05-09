"""Kida template infrastructure and one-shot branded output.

Provides the shared template environment, thread-safe stderr writer, and
branded renders for CLI commands (error, info, check, traceback) and access
logs.  Server lifecycle output (banner, ready, shutdown, reload, worker
events) is handled by the dispatch-driven view layer in ``_state.py``.

"""

import logging
import sys
import sysconfig
import threading
import traceback as tb_module
from datetime import UTC, datetime
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Lazy-loaded kida environment
_env = None
_env_lock = threading.Lock()

logger = logging.getLogger("pounce")


def _get_env():
    """Get or create the kida template environment (thread-safe, lazy)."""
    global _env
    if _env is not None:
        return _env
    with _env_lock:
        if _env is not None:
            return _env
        from kida import FileSystemLoader
        from milo.templates import get_env

        _env = get_env(loader=FileSystemLoader(str(_TEMPLATE_DIR)), terminal_color=True)
        return _env


def _is_pretty() -> bool:
    """Check if we're in pretty (TTY) output mode."""
    from pounce.logging import is_pretty

    return is_pretty()


def _render(name: str, **ctx) -> str:
    """Render a kida template with the given context."""
    return _get_env().get_template(name).render(**ctx)


def _write(text: str) -> None:
    """Write a line to stderr (thread-safe).

    Reuses the shared lock from pounce.logging so lifecycle output and
    direct stderr writes (JSON mode, etc.) never interleave under
    free-threaded Python.
    """
    from pounce.logging import stderr_lock

    with stderr_lock:
        sys.stderr.write(text + "\n")


# ── Errors ────────────────────────────────────────────


def error(
    message: str,
    *,
    code: str | None = None,
    hint: str | None = None,
    docs_url: str | None = None,
    diagnostics: list[dict[str, str]] | None = None,
) -> None:
    """Render a branded error message.

    Uses the kida template when stderr is a TTY (even if logging hasn't
    been configured yet — errors can fire before configure_logging runs).
    Falls back to plain text when piped.
    """
    import os

    use_pretty = _is_pretty() or sys.stderr.isatty() or os.environ.get("FORCE_COLOR") == "1"
    if use_pretty:
        _write(
            _render(
                "error.kida",
                error=message,
                code=code or "",
                hint=hint or "",
                template_name="",
                docs_url=docs_url or "",
                diagnostics=diagnostics or [],
            )
        )
    else:
        parts = [f"Error: {message}"]
        if code:
            parts.append(f"Code: {code}")
        if diagnostics:
            parts.extend(f"{d['label']}: {d['value']}" for d in diagnostics)
        if hint:
            parts.append(f"Hint: {hint}")
        if docs_url:
            parts.append(f"See: {docs_url}")
        _write("  ".join(parts))


# ── Access log ────────────────────────────────────────


def _human_bytes(n: int) -> str:
    """Format byte count for human readability."""
    if n < 1000:
        return f"{n}B"
    if n < 1_000_000:
        return f"{n / 1000:.1f}kB"
    return f"{n / 1_000_000:.1f}MB"


def _duration_str(ms: float) -> str:
    """Format duration in human-readable form."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def access(
    method: str,
    path: str,
    status: int,
    bytes_sent: int,
    duration_ms: float,
    client: str,
) -> None:
    """Render a pretty-mode access log line via kida template."""
    ts = datetime.now(tz=UTC).strftime("%H:%M:%S")
    size = _human_bytes(bytes_sent) if bytes_sent > 0 else ""
    duration = _duration_str(duration_ms)
    # Pre-pad strings since kida doesn't have ljust/rjust filters
    _write(
        _render(
            "access.kida",
            ts=ts,
            method=f"{method:<5s}",
            path=path,
            status=status,
            size=f"{size:>7s}",
            duration=f"{duration:>6s}",
        )
    )


# ── GIL detection ─────────────────────────────────────


def detect_gil_status() -> str:
    """Detect whether the GIL is enabled or disabled."""
    try:
        return "nogil" if sysconfig.get_config_var("Py_GIL_DISABLED") else "GIL"
    except Exception:
        return "unknown"


# ── Dependency probing ───────────────────────────────


_OPTIONAL_DEPS = [
    {"module": "h2", "name": "HTTP/2 (h2)", "hint": "pip install bengal-pounce[h2]"},
    {"module": "wsproto", "name": "WebSocket (wsproto)", "hint": "pip install bengal-pounce[ws]"},
    {"module": "truststore", "name": "TLS (truststore)", "hint": "pip install bengal-pounce[tls]"},
    {
        "module": "zoomies",
        "name": "HTTP/3 (bengal-zoomies)",
        "hint": "pip install bengal-pounce[h3]",
    },
]

_KNOWN_FRAMEWORKS = [
    ("fastapi", "FastAPI"),
    ("starlette", "Starlette"),
    ("litestar", "Litestar"),
    ("django", "Django"),
    ("quart", "Quart"),
    ("blacksheep", "BlackSheep"),
    ("sanic", "Sanic"),
]


def probe_optional_dep(module: str) -> tuple[bool, str]:
    """Check if an optional dependency is importable and return its version."""
    try:
        mod = __import__(module)
        version = getattr(mod, "__version__", "installed")
        return True, str(version)
    except ImportError:
        return False, ""


def probe_all_optional_deps() -> list[dict]:
    """Probe all optional dependencies, returning status dicts for rendering."""
    results = []
    for dep in _OPTIONAL_DEPS:
        installed, version = probe_optional_dep(dep["module"])
        results.append(
            {
                "name": dep["name"],
                "installed": installed,
                "version": version,
                "hint": dep["hint"],
            }
        )
    return results


def detect_frameworks() -> list[str]:
    """Detect installed ASGI frameworks by attempting imports."""
    found = []
    for module, label in _KNOWN_FRAMEWORKS:
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", "")
            found.append(f"{label} {version}".strip())
        except ImportError:
            pass
    return found


# ── Info panel ───────────────────────────────────────


def info_panel(
    *,
    version: str,
    python_version: str,
    platform_str: str,
    cpu_count: int,
    gil_status: str,
    install_path: str,
    deps: list[dict],
    frameworks: list[str],
) -> None:
    """Render the system info diagnostic panel."""
    import os

    use_pretty = _is_pretty() or sys.stderr.isatty() or os.environ.get("FORCE_COLOR") == "1"
    if use_pretty:
        _write(
            _render(
                "info.kida",
                version=version,
                python_version=python_version,
                platform=platform_str,
                cpu_count=cpu_count,
                gil_status=gil_status,
                install_path=install_path,
                deps=deps,
                frameworks=frameworks,
            )
        )
    else:
        logger.info(
            "pounce v%s | Python %s | %s | %d CPUs | %s",
            version,
            python_version,
            platform_str,
            cpu_count,
            gil_status,
        )
        for dep in deps:
            status = dep["version"] if dep["installed"] else "not installed"
            logger.info("  %s: %s", dep["name"], status)


# ── Check results ────────────────────────────────────


def check_results(
    *,
    version: str,
    checks: list[dict],
    all_passed: bool,
) -> None:
    """Render pre-flight check results."""
    import os

    use_pretty = _is_pretty() or sys.stderr.isatty() or os.environ.get("FORCE_COLOR") == "1"
    if use_pretty:
        _write(
            _render(
                "check.kida",
                version=version,
                checks=checks,
                all_passed=all_passed,
            )
        )
    else:
        # Write directly via _write (same stderr-locked writer the pretty branch
        # uses) instead of logger.info — ``pounce check`` runs before
        # ``configure_logging`` installs handlers, so logger.info is silently
        # dropped. Agents piping stderr get real output in either branch.
        for check in checks:
            icon = (
                "PASS"
                if check["status"] == "success"
                else "FAIL"
                if check["status"] == "error"
                else "WARN"
            )
            _write(f"[{icon}] {check['name']}: {check.get('detail', '')}")
        if all_passed:
            _write("All checks passed.")


# ── Branded tracebacks ───────────────────────────────


def _shorten_path(filepath: str) -> str:
    """Shorten a file path for display."""
    path = filepath
    # Strip site-packages prefix
    sp = "site-packages/"
    idx = path.find(sp)
    if idx != -1:
        path = path[idx + len(sp) :]
    else:
        # Strip home directory
        home = str(Path.home())
        if path.startswith(home):
            path = "~" + path[len(home) :]
    return path


def _hint_for_crash(exc: BaseException) -> str:
    """Generate a contextual hint for common crash patterns."""
    msg = str(exc).lower()
    if isinstance(exc, KeyError) and "state" in msg:
        return "Missing lifespan state -- ensure your app populates state during startup."
    if isinstance(exc, ImportError):
        return "A required module is missing. Check your dependencies."
    if "connection" in msg and "refused" in msg:
        return "A backend service is unreachable. Check database/cache connections."
    if isinstance(exc, MemoryError):
        return "Out of memory -- consider reducing --workers or increasing available RAM."
    if "codec" in msg or "encode" in msg or "decode" in msg:
        return "Text encoding error -- check response content type and encoding."
    if isinstance(exc, PermissionError):
        return "Permission denied -- check file or socket permissions."
    return ""


def branded_traceback(
    exc: BaseException,
    *,
    worker_id: int | None = None,
) -> None:
    """Render a branded traceback for an unhandled exception."""
    import os

    exc_type = type(exc).__name__
    exc_message = str(exc)

    tb = exc.__traceback__
    raw_frames = tb_module.extract_tb(tb) if tb else []

    # Take last 10 frames for display
    display_frames = raw_frames[-10:]
    frames = []
    for i, frame in enumerate(display_frames):
        frames.append(
            {
                "filename": _shorten_path(frame.filename),
                "lineno": frame.lineno,
                "name": frame.name,
                "line": frame.line or "",
                "is_last": i == len(display_frames) - 1,
            }
        )

    hint = _hint_for_crash(exc)

    use_pretty = _is_pretty() or sys.stderr.isatty() or os.environ.get("FORCE_COLOR") == "1"
    if use_pretty:
        _write(
            _render(
                "traceback.kida",
                exc_type=exc_type,
                exc_message=exc_message,
                frames=frames,
                worker_id=worker_id,
                hint=hint,
            )
        )
    else:
        logger.error("%s: %s", exc_type, exc_message)
        for frame in frames:
            logger.error("  %s:%d in %s", frame["filename"], frame["lineno"], frame["name"])
