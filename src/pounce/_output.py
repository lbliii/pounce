"""Kida-powered output for pounce server lifecycle events.

Renders branded, styled terminal output for key server lifecycle moments.
Only activates in "pretty" mode (interactive TTY). JSON and text modes
fall through to their existing behavior via stdlib logging.

"""

import logging
import sys
import sysconfig
import threading
from datetime import UTC, datetime
from pathlib import Path

from pounce import __version__

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
    from pounce import logging as pounce_logging

    return pounce_logging._resolved_format == "pretty"


def _render(name: str, **ctx) -> str:
    """Render a kida template with the given context."""
    return _get_env().get_template(name).render(**ctx)


def _write(text: str) -> None:
    """Write a line to stderr (thread-safe).

    Reuses the shared lock from pounce.logging so lifecycle output and
    direct stderr writes (JSON mode, etc.) never interleave under
    free-threaded Python.
    """
    from pounce.logging import _stderr_lock

    with _stderr_lock:
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
        if diagnostics:
            parts.extend(f"{d['label']}: {d['value']}" for d in diagnostics)
        if hint:
            parts.append(f"Hint: {hint}")
        _write("  ".join(parts))


# ── Startup ───────────────────────────────────────────


def banner(config, effective_workers: int, mode_label: str, gil_status: str) -> None:
    """Render the startup banner.

    Pretty mode renders the branded kida template. Text mode prints a
    plain summary line. JSON mode is handled inline in server.py before
    this function is called.
    """
    if not _is_pretty():
        scheme = "https" if config.ssl_certfile else "http"
        url = f"{scheme}://{config.host}:{config.port}"
        logger.info(
            "pounce v%s | %s | %d %s worker(s) | %s | Python %s",
            __version__,
            url,
            effective_workers,
            mode_label,
            gil_status,
            sys.version.split()[0],
        )
        return

    scheme = "https" if config.ssl_certfile else "http"
    url = f"{scheme}://{config.host}:{config.port}"

    # Build features list: (name, enabled, detail)
    features = []
    if config.ssl_certfile:
        features.append({"name": "TLS", "on": True, "detail": ""})
    if config.http3_enabled:
        features.append({"name": "HTTP/3", "on": True, "detail": "QUIC/UDP"})
    features.append(
        {
            "name": "Compression",
            "on": config.compression,
            "detail": "" if config.compression else "disabled",
        }
    )
    features.append(
        {
            "name": "Access log",
            "on": config.access_log,
            "detail": "" if config.access_log else "disabled",
        }
    )
    if config.server_timing:
        features.append({"name": "Server-Timing", "on": True, "detail": ""})
    if config.reload:
        features.append({"name": "Reload", "on": True, "detail": "watching for changes"})

    # Build hints list
    hints = _startup_hints(config, effective_workers)

    text = _render(
        "serve_banner.kida",
        version=__version__,
        gil=gil_status,
        url=url,
        uds=config.uds,
        workers=effective_workers,
        mode=mode_label,
        log_level=config.log_level,
        features=features,
        hints=hints,
        health_check_path=config.health_check_path or "",
        root_path=config.root_path or "",
    )
    _write(text)


def _startup_hints(config, effective_workers: int) -> list[str]:
    """Generate smart startup hints based on config vs environment."""
    import os

    hints = []
    cpu_count = os.cpu_count() or 1
    if effective_workers == 1 and cpu_count >= 4:
        hints.append(f"{cpu_count} cores detected — try --workers 0 for auto-scaling")
    if not config.compression:
        hints.append("Compression is disabled; remove --no-compression for smaller responses")
    if config.reload and config.workers > 1:
        hints.append("Reload with multiple workers uses full restart, not rolling")
    return hints


def ready(host: str, port: int, *, uds: str | None = None) -> None:
    """Render the 'ready to accept connections' message."""
    if _is_pretty():
        address = uds if uds else f"{host}:{port}"
        _write(_render("ready.kida", address=address))
    else:
        logger.info("Ready to accept connections")


# ── Shutdown ──────────────────────────────────────────


def shutdown_start(connections: int = 0) -> None:
    """Render the shutdown initiation message."""
    if _is_pretty():
        _write(_render("shutdown.kida", phase="start", connections=connections, timeout=0))
    else:
        logger.info("Shutting down — draining connections...")


def shutdown_drained() -> None:
    """Render the 'all connections drained' message."""
    if _is_pretty():
        _write(_render("shutdown.kida", phase="drained", connections=0, timeout=0))
    else:
        logger.info("All connections drained")


def shutdown_timeout(timeout: float) -> None:
    """Render the drain timeout warning."""
    if _is_pretty():
        _write(_render("shutdown.kida", phase="timeout", connections=0, timeout=timeout))
    else:
        logger.warning("Shutdown timeout (%.1fs) — forcing remaining connections closed", timeout)


def shutdown_complete() -> None:
    """Render the final server stopped message."""
    if _is_pretty():
        _write(_render("shutdown.kida", phase="complete", connections=0, timeout=0))
    else:
        logger.info("Pounce server stopped")


# ── Reload ────────────────────────────────────────────


def reload_detected(changed_files: list[str]) -> None:
    """Render the file change detection message."""
    if _is_pretty():
        # Truncate to first 5 files for display
        display = changed_files[:5]
        if len(changed_files) > 5:
            display.append(f"+{len(changed_files) - 5} more")
        _write(
            _render(
                "reload.kida", phase="detected", files=display, generation=0, error="", workers=0
            )
        )
    else:
        if len(changed_files) <= 5:
            logger.info(
                "Detected %d changed file(s): %s", len(changed_files), ", ".join(changed_files)
            )
        else:
            logger.info(
                "Detected %d changed file(s): %s...",
                len(changed_files),
                ", ".join(changed_files[:5]),
            )


def reload_start() -> None:
    """Render the reload initiation message."""
    if _is_pretty():
        _write(_render("reload.kida", phase="start", files=[], generation=0, error="", workers=0))
    else:
        logger.info("Reloading...")


def reload_complete(workers: int = 0, generation: int | None = None) -> None:
    """Render the reload completion message."""
    if _is_pretty():
        _write(
            _render(
                "reload.kida",
                phase="complete",
                files=[],
                generation=generation or 0,
                error="",
                workers=workers,
            )
        )
    else:
        if generation is not None:
            logger.info(
                "Graceful reload complete. Running %d worker(s) on generation %d",
                workers,
                generation,
            )
        else:
            logger.info("All %d worker(s) restarted", workers)


def reload_failed(error: str = "") -> None:
    """Render the reload failure message."""
    if _is_pretty():
        _write(
            _render("reload.kida", phase="failed", files=[], generation=0, error=error, workers=0)
        )
    else:
        logger.info("Reload failed — serving previous version")


# ── Worker events ─────────────────────────────────────


def supervisor_starting(count: int, mode: str) -> None:
    """Render the supervisor startup message."""
    if _is_pretty():
        _write(
            _render(
                "worker_event.kida",
                event="supervisor_start",
                id=0,
                mode=mode,
                count=count,
                restarts=0,
                generation=0,
            )
        )
    else:
        logger.info("Supervisor starting %d %s worker(s)", count, mode)


def worker_started(worker_id: int, mode: str, generation: int | None = None) -> None:
    """Render the worker started message."""
    if _is_pretty():
        _write(
            _render(
                "worker_event.kida",
                event="started",
                id=worker_id,
                mode=mode,
                count=0,
                restarts=0,
                generation=generation or 0,
            )
        )
    else:
        logger.debug("Started worker %d (%s)", worker_id, mode)


def worker_crashed(worker_id: int, restart_count: int) -> None:
    """Render the worker crash message."""
    if _is_pretty():
        _write(
            _render(
                "worker_event.kida",
                event="crashed",
                id=worker_id,
                mode="",
                count=0,
                restarts=restart_count,
                generation=0,
            )
        )
    else:
        logger.warning("Worker %d crashed, restarting (restart #%d)", worker_id, restart_count)


def worker_max_restarts(worker_id: int, max_restarts: int) -> None:
    """Render the max restarts exceeded message."""
    if _is_pretty():
        _write(
            _render(
                "worker_event.kida",
                event="max_restarts",
                id=worker_id,
                mode="",
                count=0,
                restarts=max_restarts,
                generation=0,
            )
        )
    else:
        logger.error(
            "Worker %d exceeded max restarts (%d) — not restarting", worker_id, max_restarts
        )


def supervisor_shutdown(count: int) -> None:
    """Render the supervisor shutdown message."""
    if _is_pretty():
        _write(
            _render(
                "worker_event.kida",
                event="supervisor_shutdown",
                id=0,
                mode="",
                count=count,
                restarts=0,
                generation=0,
            )
        )
    else:
        logger.info("Shutting down %d worker(s)...", count)


def supervisor_all_stopped() -> None:
    """Render the 'all workers stopped' message."""
    if _is_pretty():
        _write(
            _render(
                "worker_event.kida",
                event="all_stopped",
                id=0,
                mode="",
                count=0,
                restarts=0,
                generation=0,
            )
        )
    else:
        logger.info("All workers stopped")


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
            path=f"{path:<30s}",
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
