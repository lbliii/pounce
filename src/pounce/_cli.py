"""
Command-line interface for pounce.

Provides the ``pounce`` command with subcommands::

    pounce serve --app myapp:app --host 0.0.0.0 --port 8000 --workers 4

Built on milo-cli for type-driven parsing, MCP server, and llms.txt generation.

"""

import argparse
import sys
from pathlib import Path

from milo.commands import CLI

from pounce import __version__
from pounce._bench import _BENCH_HELP, register_bench_command
from pounce._importer import import_app
from pounce.config import ServerConfig
from pounce.display import CliDisplayOverrides
from pounce.server import Server


def _render_branded_help(parser: argparse.ArgumentParser) -> str:
    """Render help for a parser through pounce's help.kida template."""
    from pounce._output import _get_env

    env = _get_env()
    template = env.get_template("help.kida")

    from milo.help import HelpState

    # Build subcommand help text from parser choices.
    # Argparse names the subparser dest based on the parser group: the top-level
    # pounce parser uses ``_command``, while ``cli.group("config", ...)`` builds
    # a nested parser whose dest is ``_command_config``. Accept both shapes so
    # nested command groups render their subcommands instead of leaking the
    # internal dest as a positional.
    _sub_help = {}
    for action_group in parser._action_groups:
        for action in action_group._group_actions:
            is_subcommand = action.dest == "_command" or action.dest.startswith("_command_")
            if is_subcommand and isinstance(action.choices, dict):
                for name, sp in action.choices.items():
                    if isinstance(sp, argparse.ArgumentParser):
                        # Use the 'help' kwarg from add_parser(), stored on the action
                        _sub_help[name] = ""
                # Also check the subparser action's _choices_actions for help text
                for choice_action in getattr(action, "_choices_actions", []):
                    _sub_help[choice_action.dest] = choice_action.help or ""

    groups = []
    for action_group in parser._action_groups:
        actions = []
        for action in action_group._group_actions:
            choices = action.choices
            is_subcommand = action.dest == "_command" or action.dest.startswith("_command_")
            if is_subcommand and isinstance(choices, dict):
                choices = {name: _sub_help.get(name, "") for name in choices}
            actions.append(
                {
                    "option_strings": action.option_strings,
                    "dest": action.dest,
                    "help": action.help or "",
                    "default": action.default,
                    "required": getattr(action, "required", False),
                    "choices": choices,
                    "nargs": action.nargs,
                    "metavar": action.metavar,
                }
            )
        if actions:
            groups.append({"title": action_group.title or "", "actions": actions})

    state = HelpState(
        prog=parser.prog,
        description=parser.description or "",
        groups=tuple(groups),
        epilog=_config_epilog_for(parser.prog),
    )
    return template.render(state=state)


_CONFIG_EPILOG = (
    "Any ServerConfig field can also be set in pounce.toml or pyproject.toml "
    "under [tool.pounce]. Run 'pounce config schema --output-format toml-template' "
    "for the full list."
)


def _config_epilog_for(prog: str) -> str:
    """Return the TOML escape-hatch footer for serve/check help."""
    if prog.endswith((" serve", " check")):
        return _CONFIG_EPILOG
    return ""


def _install_branded_help(parser: argparse.ArgumentParser) -> None:
    """Patch format_help on a parser and all its subparsers to use branded templates."""
    original = parser.format_help

    def branded_format_help() -> str:
        try:
            return _render_branded_help(parser)
        except Exception:
            return original()

    parser.format_help = branded_format_help  # ty: ignore[invalid-assignment]

    if parser._subparsers:
        for action in parser._subparsers._actions:
            choices = getattr(action, "choices", None)
            if not isinstance(choices, dict):
                continue
            for sp in choices.values():
                _install_branded_help(sp)


# Help text for serve arguments (milo doesn't propagate these from annotations).
_SERVE_HELP = {
    "app": "ASGI application (e.g., 'myapp:app' or 'myapp:create_app()')",
    "config": "Path to config file (pounce.toml or pyproject.toml); auto-detected if omitted",
    "host": "Bind address",
    "port": "Bind port",
    "workers": "Number of workers; 0 = auto-detect",
    "worker_mode": "Worker model: auto, sync, async, or subinterpreter (beta)",
    "cpu_affinity": "Pin each worker to a CPU core (Linux only)",
    "log_level": "Log level",
    "log_format": "Log format: auto, text, or json",
    "root_path": "ASGI root_path for reverse proxy setups",
    "no_compression": "Disable response compression (config file: compression = false)",
    "server_timing": "Enable Server-Timing header injection",
    "no_access_log": "Disable access logging (config file: access_log = false)",
    "ssl_certfile": "Path to TLS certificate file (enables HTTPS)",
    "ssl_keyfile": "Path to TLS private key file",
    "no_http2": "Disable h2 ALPN advertisement; force HTTP/1.1 at the TLS origin",
    "http3": "Enable HTTP/3 (QUIC/UDP); requires TLS (config: http3_enabled)",
    "reload": "Auto-reload on source file changes",
    "reload_include": "Extra file extensions to watch (comma-separated)",
    "reload_dir": "Extra directories to watch (repeatable) (config: reload_dirs)",
    "keep_alive_timeout": "Idle keep-alive timeout in seconds",
    "header_timeout": "Header receive timeout in seconds",
    "request_timeout": "Request body receive timeout in seconds",
    "write_timeout": "Blocked response write timeout in seconds",
    "startup_timeout": "Max seconds to wait for app lifespan startup",
    "max_requests_per_connection": "Max requests per connection; 0 = unlimited",
    "shutdown_timeout": "Max seconds per worker during shutdown",
    "uds": "Unix domain socket path",
    "health_check_path": "Built-in readiness endpoint path",
    "debug": "Enable rich error pages (never use in production!)",
    "trusted_hosts": "Comma-separated trusted hostnames for X-Forwarded-* headers",
    "metrics": "Enable Prometheus /metrics endpoint (config: metrics_enabled)",
    "app_name": "Application name shown in the startup banner",
    "app_tagline": "Short description shown under the application name",
    "app_version": "Application version string for the startup banner",
    "signage": "Banner layout: full, minimal, or off (pretty mode only)",
}


_CHECK_HELP = {
    **_SERVE_HELP,
}

_INFO_HELP = {
    "app": "ASGI application (optional, for framework detection)",
}

# Merge all help dicts for subparser enrichment
_ALL_HELP = {**_SERVE_HELP, **_CHECK_HELP, **_INFO_HELP, **_BENCH_HELP}


def _enrich_subparser_help(parser: argparse.ArgumentParser) -> None:
    """Add help text to subparser arguments that milo left blank."""
    if parser._subparsers:
        for action in parser._subparsers._actions:
            choices = getattr(action, "choices", None)
            if not isinstance(choices, dict):
                continue
            for sp in choices.values():
                for ag in sp._action_groups:
                    for a in ag._group_actions:
                        if not a.help and a.dest in _ALL_HELP:
                            a.help = _ALL_HELP[a.dest]


class _PounceCLI(CLI):
    """CLI subclass that enables branded help output."""

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        _enrich_subparser_help(parser)
        _install_branded_help(parser)
        return parser


cli = _PounceCLI(
    name="pounce",
    description="A free-threading-native ASGI server for Python 3.14t",
    version=__version__,
)

register_bench_command(cli)


@cli.command("serve", description="Start the ASGI server", display_result=False)
def serve(
    app: str,
    config: str | None = None,
    host: str | None = None,
    port: int | None = None,
    workers: int | None = None,
    worker_mode: str | None = None,
    cpu_affinity: bool = False,
    log_level: str | None = None,
    log_format: str | None = None,
    root_path: str | None = None,
    no_compression: bool = False,
    server_timing: bool = False,
    no_access_log: bool = False,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    no_http2: bool = False,
    http3: bool = False,
    reload: bool = False,
    reload_include: str | None = None,
    reload_dir: list[str] | None = None,
    keep_alive_timeout: float | None = None,
    header_timeout: float | None = None,
    request_timeout: float | None = None,
    write_timeout: float | None = None,
    startup_timeout: float | None = None,
    max_requests_per_connection: int | None = None,
    shutdown_timeout: float | None = None,
    uds: str | None = None,
    health_check_path: str | None = None,
    debug: bool = False,
    trusted_hosts: str | None = None,
    metrics: bool = False,
    app_name: str | None = None,
    app_tagline: str | None = None,
    app_version: str | None = None,
    signage: str | None = None,
) -> None:
    """Start the ASGI server.

    Accepts an ASGI application reference (e.g., 'myapp:app' or
    'myapp:create_app()') and starts serving it.
    """
    # Ensure current directory is on sys.path so that local modules
    # can be imported (e.g., ``pounce serve --app myapp:app`` from the project dir).
    if "" not in sys.path and "." not in sys.path:
        sys.path.insert(0, ".")

    try:
        _serve_impl(
            app=app,
            config=config,
            host=host,
            port=port,
            workers=workers,
            log_level=log_level,
            log_format=log_format,
            root_path=root_path,
            no_compression=no_compression,
            server_timing=server_timing,
            no_access_log=no_access_log,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            no_http2=no_http2,
            http3=http3,
            reload=reload,
            reload_include=reload_include,
            reload_dir=reload_dir,
            keep_alive_timeout=keep_alive_timeout,
            header_timeout=header_timeout,
            request_timeout=request_timeout,
            write_timeout=write_timeout,
            startup_timeout=startup_timeout,
            max_requests_per_connection=max_requests_per_connection,
            shutdown_timeout=shutdown_timeout,
            uds=uds,
            health_check_path=health_check_path,
            debug=debug,
            trusted_hosts=trusted_hosts,
            metrics=metrics,
            worker_mode=worker_mode,
            cpu_affinity=cpu_affinity,
            app_name=app_name,
            app_tagline=app_tagline,
            app_version=app_version,
            signage=signage,
        )
    except KeyboardInterrupt:
        pass
    except (ValueError, ImportError, AttributeError, TypeError) as exc:
        _die(
            str(exc),
            hint=_hint_for_import_error(exc),
            diagnostics=_diagnostics_for_import_error(exc),
        )
    except OSError as exc:
        _die(
            str(exc),
            hint=_hint_for_os_error(exc),
            diagnostics=_diagnostics_for_os_error(exc),
        )
    except Exception as exc:
        # Catch PounceError subtypes (TLSError, LifespanError, etc.)
        # and any other unexpected errors
        from pounce._errors import PounceError

        if isinstance(exc, PounceError):
            _die(
                str(exc),
                hint=exc.hint or _hint_for_pounce_error(exc),
                code=exc.code,
                doc=exc.doc,
            )
        else:
            from pounce import _output

            _output.branded_traceback(exc)
            sys.exit(1)


def _serve_impl(
    *,
    app: str,
    config: str | None,
    host: str | None,
    port: int | None,
    workers: int | None,
    log_level: str | None,
    log_format: str | None,
    root_path: str | None,
    no_compression: bool,
    server_timing: bool,
    no_access_log: bool,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    no_http2: bool,
    http3: bool,
    reload: bool,
    reload_include: str | None,
    reload_dir: list[str] | None,
    keep_alive_timeout: float | None,
    header_timeout: float | None,
    request_timeout: float | None,
    write_timeout: float | None,
    startup_timeout: float | None,
    max_requests_per_connection: int | None,
    shutdown_timeout: float | None,
    uds: str | None,
    health_check_path: str | None,
    debug: bool,
    trusted_hosts: str | None,
    metrics: bool,
    worker_mode: str | None,
    cpu_affinity: bool,
    app_name: str | None,
    app_tagline: str | None,
    app_version: str | None,
    signage: str | None,
) -> None:
    """Inner serve implementation — raises on error, no catching."""
    from pounce._config_file import load_config_with_overrides

    asgi_app = import_app(app)

    # Build CLI overrides from explicitly-provided arguments only.
    # None means "not provided" — let TOML or ServerConfig defaults fill in.
    # Boolean flags use False as "not provided" since --store-true can only set True.
    parsed_reload_include = parse_extensions(reload_include)
    parsed_reload_dirs = parse_dirs(reload_dir)

    cli_overrides: dict[str, object] = {}
    if host is not None:
        cli_overrides["host"] = host
    if port is not None:
        cli_overrides["port"] = port
    if workers is not None:
        cli_overrides["workers"] = workers
    if log_level is not None:
        cli_overrides["log_level"] = log_level
    if log_format is not None:
        cli_overrides["log_format"] = log_format
    if root_path is not None:
        cli_overrides["root_path"] = root_path
    if no_compression:
        cli_overrides["compression"] = False
    if server_timing:
        cli_overrides["server_timing"] = True
    if no_access_log:
        cli_overrides["access_log"] = False
    if ssl_certfile is not None:
        cli_overrides["ssl_certfile"] = ssl_certfile
    if ssl_keyfile is not None:
        cli_overrides["ssl_keyfile"] = ssl_keyfile
    if no_http2:
        cli_overrides["http2_enabled"] = False
    if http3:
        cli_overrides["http3_enabled"] = True
    if reload:
        cli_overrides["reload"] = True
    if parsed_reload_include:
        cli_overrides["reload_include"] = parsed_reload_include
    if parsed_reload_dirs:
        cli_overrides["reload_dirs"] = parsed_reload_dirs
    if keep_alive_timeout is not None:
        cli_overrides["keep_alive_timeout"] = keep_alive_timeout
    if header_timeout is not None:
        cli_overrides["header_timeout"] = header_timeout
    if request_timeout is not None:
        cli_overrides["request_timeout"] = request_timeout
    if write_timeout is not None:
        cli_overrides["write_timeout"] = write_timeout
    if startup_timeout is not None:
        cli_overrides["startup_timeout"] = startup_timeout
    if max_requests_per_connection is not None:
        cli_overrides["max_requests_per_connection"] = max_requests_per_connection
    if shutdown_timeout is not None:
        cli_overrides["shutdown_timeout"] = shutdown_timeout
    if uds is not None:
        cli_overrides["uds"] = uds
    if health_check_path is not None:
        cli_overrides["health_check_path"] = health_check_path
    if debug:
        cli_overrides["debug"] = True
    parsed_trusted_hosts = parse_hosts(trusted_hosts)
    if parsed_trusted_hosts:
        cli_overrides["trusted_hosts"] = list(parsed_trusted_hosts)
    if metrics:
        cli_overrides["metrics_enabled"] = True
    if worker_mode is not None:
        cli_overrides["worker_mode"] = worker_mode
    if cpu_affinity:
        cli_overrides["cpu_affinity"] = True

    # Early validation for mutually exclusive / co-dependent options.
    if http3 and uds is not None:
        msg = "--http3 cannot be used with --uds (HTTP/3 requires UDP, not Unix domain sockets)"
        raise ValueError(msg)
    if ssl_certfile is not None and ssl_keyfile is None:
        msg = "--ssl-certfile requires --ssl-keyfile"
        raise ValueError(msg)
    if ssl_keyfile is not None and ssl_certfile is None:
        msg = "--ssl-keyfile requires --ssl-certfile"
        raise ValueError(msg)

    config_path = Path(config) if config else None
    merged = load_config_with_overrides(cli_overrides, config_path=config_path)

    server_config = ServerConfig.from_mapping(merged)

    # Merge branding: CLI flags override config-file values.
    effective_name = app_name or server_config.app_name
    effective_tagline = app_tagline or server_config.app_tagline
    effective_version = app_version or server_config.app_version
    effective_signage = signage or server_config.signage

    cli_display = (
        CliDisplayOverrides(
            name=effective_name,
            tagline=effective_tagline,
            version=effective_version,
            signage=effective_signage,
        )
        if any((effective_name, effective_tagline, effective_version, effective_signage))
        else None
    )

    server = Server(server_config, asgi_app, app_path=app, cli_display=cli_display)
    server.run()


# ── Error hints ──────────────────────────────────────────


def _die(
    message: str,
    *,
    hint: str | None = None,
    code: str | None = None,
    doc: str | None = None,
    diagnostics: list[dict[str, str]] | None = None,
) -> None:
    """Render a branded error and exit.

    ``code`` is a semantic ``POUNCE_<CATEGORY>_<SPECIFIC>`` identifier; ``doc``
    is the troubleshooting anchor (e.g. ``docs/troubleshooting.md#POUNCE_X_Y``).
    Both are surfaced through ``_output.error`` to ``error.kida`` for rendering
    and let an agent reading stderr navigate from a failure to its catalog
    entry without grepping.
    """
    from pounce import _output

    _output.error(message, hint=hint, code=code, docs_url=doc, diagnostics=diagnostics)
    sys.exit(1)


def _hint_for_import_error(exc: Exception) -> str | None:
    """Return a hint for common import/app-path errors."""
    msg = str(exc).lower()
    if "no module named" in msg or "could not import" in msg:
        return (
            "Check that the module is installed and on sys.path (run from the project directory)."
        )
    if "no attribute" in msg:
        return "Verify the attribute name matches what's exported from the module."
    if "not callable" in msg:
        return "The app must be an async callable. Use 'module:factory()' for factory functions."
    if "expected format" in msg:
        return "Use the format 'module:attribute' (e.g., 'myapp:app')."
    return None


def _diagnostics_for_import_error(exc: Exception) -> list[dict[str, str]] | None:
    """Return diagnostic context for import errors."""
    msg = str(exc).lower()
    if "no module named" in msg or "could not import" in msg:
        cwd = __import__("os").getcwd()
        return [{"label": "Working dir", "value": cwd}]
    return None


def _hint_for_os_error(exc: OSError) -> str | None:
    """Return a hint for common OS-level errors."""
    import errno

    if exc.errno == errno.EADDRINUSE or "already in use" in str(exc):
        return "Kill the other process or use a different --port."
    if exc.errno == errno.EACCES or "permission denied" in str(exc).lower():
        return "Use a port > 1024 or run with elevated permissions."
    if "could not resolve" in str(exc).lower():
        return "Check that the --host address is valid on this machine."
    return None


def _diagnostics_for_os_error(exc: OSError) -> list[dict[str, str]] | None:
    """Return diagnostic context for OS errors (e.g. who holds the port)."""
    import errno
    import re
    import subprocess

    if exc.errno != errno.EADDRINUSE and "already in use" not in str(exc):
        return None

    # Extract port from error message
    port_match = re.search(r":(\d+)", str(exc))
    if not port_match:
        return None
    port = port_match.group(1)

    # Try lsof to find what's using the port
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            pid = pids[0]
            # Get process name
            ps_result = subprocess.run(
                ["ps", "-p", pid, "-o", "comm="],
                capture_output=True,
                text=True,
                timeout=2,
            )
            proc_name = ps_result.stdout.strip() or "unknown"
            return [
                {"label": "PID using port", "value": f"{pid} ({proc_name})"},
                {"label": "Quick fix", "value": f"kill {pid}"},
            ]
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        pass
    return None


def _hint_for_pounce_error(exc: Exception) -> str | None:
    """Return a hint for pounce-specific errors."""
    from pounce._errors import LifespanError, TLSError

    if isinstance(exc, TLSError):
        msg = str(exc).lower()
        if "not found" in msg:
            return "Check the --ssl-certfile and --ssl-keyfile paths."
        if "permission" in msg:
            return "Check file permissions on the TLS certificate and key."
        return "Verify the certificate and key are valid PEM files."
    if isinstance(exc, LifespanError):
        return "The app's lifespan handler raised during startup. Check app initialization."
    return None


def parse_extensions(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated extensions string into a normalized tuple.

    Ensures each extension starts with a dot and strips whitespace.
    Empty entries are filtered out.

    Args:
        raw: Comma-separated string (e.g. ``".html,.css,md"``), or None.

    Returns:
        Tuple of normalized extensions (e.g. ``(".html", ".css", ".md")``).

    """
    if not raw:
        return ()
    return tuple(
        ext.strip() if ext.strip().startswith(".") else f".{ext.strip()}"
        for ext in raw.split(",")
        if ext.strip()
    )


def parse_dirs(raw: list[str] | None) -> tuple[str, ...]:
    """Parse a list of directory strings into a cleaned tuple.

    Strips whitespace and filters empty entries.

    Args:
        raw: List of directory paths, or None.

    Returns:
        Tuple of cleaned directory paths.

    """
    if not raw:
        return ()
    return tuple(d.strip() for d in raw if d.strip())


def parse_hosts(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated host list into a normalized tuple.

    Args:
        raw: Comma-separated hostnames (e.g. ``"localhost,127.0.0.1"``), or None.

    Returns:
        Tuple of trimmed host strings.

    """
    if not raw:
        return ()
    return tuple(h.strip() for h in raw.split(",") if h.strip())


@cli.command(
    "info",
    description="Show system diagnostics and dependency status",
    display_result=False,
)
def info(output_format: str = "text") -> None:
    """Display system info, dependency status, and environment diagnostics.

    Pass ``--output-format json`` for a stable, machine-readable dict suitable
    for ``pounce info --output-format json | jq``.
    """
    import os
    import platform

    from pounce import _output
    from pounce.config import ServerConfig

    python_version = sys.version.split()[0]
    gil_status = _output.detect_gil_status()
    cpu_count = os.cpu_count() or 1
    platform_str = platform.platform()
    install_path = str(Path(__file__).parent)

    deps = _output.probe_all_optional_deps()
    frameworks = _output.detect_frameworks()

    worker_model = _output.detect_worker_model()
    worker_count = ServerConfig().resolve_workers()

    _output.info_panel(
        version=__version__,
        python_version=python_version,
        platform_str=platform_str,
        cpu_count=cpu_count,
        gil_status=gil_status,
        install_path=install_path,
        deps=deps,
        frameworks=frameworks,
        worker_model=worker_model,
        worker_count=worker_count,
        output_format=output_format,
    )


@cli.command(
    "check",
    description="Validate configuration before starting",
    display_result=False,
)
def check(
    app: str,
    config: str | None = None,
    host: str | None = None,
    port: int | None = None,
    workers: int | None = None,
    worker_mode: str | None = None,
    cpu_affinity: bool = False,
    log_level: str | None = None,
    log_format: str | None = None,
    root_path: str | None = None,
    no_compression: bool = False,
    server_timing: bool = False,
    no_access_log: bool = False,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    no_http2: bool = False,
    http3: bool = False,
    reload: bool = False,
    reload_include: str | None = None,
    reload_dir: list[str] | None = None,
    keep_alive_timeout: float | None = None,
    header_timeout: float | None = None,
    request_timeout: float | None = None,
    write_timeout: float | None = None,
    startup_timeout: float | None = None,
    max_requests_per_connection: int | None = None,
    shutdown_timeout: float | None = None,
    uds: str | None = None,
    health_check_path: str | None = None,
    debug: bool = False,
    trusted_hosts: str | None = None,
    metrics: bool = False,
    app_name: str | None = None,
    app_tagline: str | None = None,
    app_version: str | None = None,
    signage: str | None = None,
) -> None:
    """Run pre-flight validation checks.

    Takes the same arguments as ``serve`` and validates them without
    starting the server.  Exits with code 1 if any check fails.
    """
    if "" not in sys.path and "." not in sys.path:
        sys.path.insert(0, ".")

    from pounce import _output
    from pounce._config_file import load_config_with_overrides

    # Build CLI overrides the same way serve does — None means "not provided".
    parsed_reload_include = parse_extensions(reload_include)
    parsed_reload_dirs = parse_dirs(reload_dir)

    cli_overrides: dict[str, object] = {}
    if host is not None:
        cli_overrides["host"] = host
    if port is not None:
        cli_overrides["port"] = port
    if workers is not None:
        cli_overrides["workers"] = workers
    if worker_mode is not None:
        cli_overrides["worker_mode"] = worker_mode
    if cpu_affinity:
        cli_overrides["cpu_affinity"] = True
    if log_level is not None:
        cli_overrides["log_level"] = log_level
    if log_format is not None:
        cli_overrides["log_format"] = log_format
    if root_path is not None:
        cli_overrides["root_path"] = root_path
    if no_compression:
        cli_overrides["compression"] = False
    if server_timing:
        cli_overrides["server_timing"] = True
    if no_access_log:
        cli_overrides["access_log"] = False
    if ssl_certfile is not None:
        cli_overrides["ssl_certfile"] = ssl_certfile
    if ssl_keyfile is not None:
        cli_overrides["ssl_keyfile"] = ssl_keyfile
    if no_http2:
        cli_overrides["http2_enabled"] = False
    if http3:
        cli_overrides["http3_enabled"] = True
    if reload:
        cli_overrides["reload"] = True
    if parsed_reload_include:
        cli_overrides["reload_include"] = parsed_reload_include
    if parsed_reload_dirs:
        cli_overrides["reload_dirs"] = parsed_reload_dirs
    if keep_alive_timeout is not None:
        cli_overrides["keep_alive_timeout"] = keep_alive_timeout
    if header_timeout is not None:
        cli_overrides["header_timeout"] = header_timeout
    if request_timeout is not None:
        cli_overrides["request_timeout"] = request_timeout
    if write_timeout is not None:
        cli_overrides["write_timeout"] = write_timeout
    if startup_timeout is not None:
        cli_overrides["startup_timeout"] = startup_timeout
    if max_requests_per_connection is not None:
        cli_overrides["max_requests_per_connection"] = max_requests_per_connection
    if shutdown_timeout is not None:
        cli_overrides["shutdown_timeout"] = shutdown_timeout
    if uds is not None:
        cli_overrides["uds"] = uds
    if health_check_path is not None:
        cli_overrides["health_check_path"] = health_check_path
    if debug:
        cli_overrides["debug"] = True
    parsed_trusted_hosts = parse_hosts(trusted_hosts)
    if parsed_trusted_hosts:
        cli_overrides["trusted_hosts"] = list(parsed_trusted_hosts)
    if metrics:
        cli_overrides["metrics_enabled"] = True

    config_path = Path(config) if config else None
    try:
        merged = load_config_with_overrides(cli_overrides, config_path=config_path)
    except ValueError as exc:
        _output.error(str(exc))
        sys.exit(1)

    # Validate config first so pre-flight checks use typed values.
    config_check = _check_merged_config_valid(merged)
    checks: list[dict[str, str]] = [_check_app_importable(app), config_check]

    if config_check["status"] != "error":
        # Config is valid — construct typed config for pre-flight checks.
        cfg = ServerConfig.from_mapping(merged)
        if not cfg.uds:
            checks.append(_check_port_available(cfg.host, cfg.port))
        if cfg.ssl_certfile:
            checks.append(_check_tls_cert(cfg.ssl_certfile, cfg.ssl_keyfile))
        checks.extend(
            _check_deps_for_config(http3=cfg.http3_enabled, ssl_certfile=cfg.ssl_certfile)
        )

    if signage is not None:
        checks.append(_check_signage(signage))

    all_passed = all(c["status"] != "error" for c in checks)
    _output.check_results(version=__version__, checks=checks, all_passed=all_passed)

    if not all_passed:
        sys.exit(1)


# ── Pre-flight check helpers ─────────────────────────────


def _check_app_importable(app: str) -> dict[str, str]:
    """Try to import the app and return a check result."""
    try:
        import_app(app)
        return {"name": "App import", "status": "success", "detail": app, "hint": ""}
    except (ValueError, ImportError, AttributeError, TypeError) as exc:
        return {
            "name": "App import",
            "status": "error",
            "detail": str(exc),
            "hint": _hint_for_import_error(exc) or "",
        }


def _check_port_available(host: str, port: int) -> dict[str, str]:
    """Try to bind the port and return a check result."""
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.close()
        return {
            "name": "Port available",
            "status": "success",
            "detail": f"{host}:{port}",
            "hint": "",
        }
    except OSError as exc:
        diagnostics = _diagnostics_for_os_error(exc)
        detail = str(exc)
        if diagnostics:
            detail += " — " + ", ".join(f"{d['label']}: {d['value']}" for d in diagnostics)
        return {
            "name": "Port available",
            "status": "error",
            "detail": detail,
            "hint": _hint_for_os_error(exc) or "",
        }


def _check_tls_cert(certfile: str, keyfile: str | None = None) -> dict[str, str]:
    """Validate TLS certificate file exists and is loadable."""
    import ssl

    cert_path = Path(certfile)
    if not cert_path.exists():
        return {
            "name": "TLS certificate",
            "status": "error",
            "detail": f"{certfile} not found",
            "hint": "Check the --ssl-certfile path.",
        }
    if not cert_path.is_file():
        return {
            "name": "TLS certificate",
            "status": "error",
            "detail": f"{certfile} is not a file",
            "hint": "Provide a path to a PEM certificate file.",
        }

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
        return {
            "name": "TLS certificate",
            "status": "success",
            "detail": certfile,
            "hint": "",
        }
    except ssl.SSLError as exc:
        return {
            "name": "TLS certificate",
            "status": "error",
            "detail": str(exc),
            "hint": "Verify the certificate and key are valid PEM files.",
        }
    except OSError as exc:
        return {
            "name": "TLS certificate",
            "status": "error",
            "detail": str(exc),
            "hint": "Check file permissions on the certificate and key.",
        }


def _check_deps_for_config(*, http3: bool, ssl_certfile: str | None) -> list[dict[str, str]]:
    """Check that optional deps are installed for requested features."""
    from pounce._output import probe_optional_dep

    checks: list[dict[str, str]] = []
    if http3:
        installed, version = probe_optional_dep("zoomies")
        if installed:
            checks.append(
                {
                    "name": "HTTP/3 dependency",
                    "status": "success",
                    "detail": f"bengal-zoomies {version}",
                    "hint": "",
                }
            )
        else:
            checks.append(
                {
                    "name": "HTTP/3 dependency",
                    "status": "error",
                    "detail": "bengal-zoomies not installed",
                    "hint": "pip install bengal-pounce[h3]",
                }
            )
    return checks


def _check_config_valid(
    *,
    host: str,
    port: int,
    workers: int,
    worker_mode: str,
    cpu_affinity: bool,
    log_level: str,
    log_format: str,
    root_path: str,
    no_compression: bool,
    server_timing: bool,
    no_access_log: bool,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    no_http2: bool,
    http3: bool,
    reload: bool,
    reload_include: str | None,
    reload_dir: list[str] | None,
    keep_alive_timeout: float,
    header_timeout: float,
    request_timeout: float,
    write_timeout: float,
    startup_timeout: float,
    max_requests_per_connection: int,
    shutdown_timeout: float,
    uds: str | None,
    health_check_path: str | None,
) -> dict[str, str]:
    """Try to construct ServerConfig and catch validation errors."""
    try:
        ServerConfig(
            host=host,
            port=port,
            workers=workers,
            worker_mode=worker_mode,
            cpu_affinity=cpu_affinity,
            log_level=log_level,
            log_format=log_format,
            root_path=root_path,
            compression=not no_compression,
            server_timing=server_timing,
            access_log=not no_access_log,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            http2_enabled=not no_http2,
            http3_enabled=http3,
            reload=reload,
            reload_include=parse_extensions(reload_include),
            reload_dirs=parse_dirs(reload_dir),
            keep_alive_timeout=keep_alive_timeout,
            header_timeout=header_timeout,
            request_timeout=request_timeout,
            write_timeout=write_timeout,
            startup_timeout=startup_timeout,
            max_requests_per_connection=max_requests_per_connection,
            shutdown_timeout=shutdown_timeout,
            uds=uds,
            health_check_path=health_check_path,
        )
        return {"name": "Config validation", "status": "success", "detail": "Valid", "hint": ""}
    except (ValueError, TypeError) as exc:
        return {
            "name": "Config validation",
            "status": "error",
            "detail": str(exc),
            "hint": "",
        }


def _check_merged_config_valid(merged: dict[str, object]) -> dict[str, str]:
    """Try to construct ServerConfig from merged config dict and catch validation errors."""
    try:
        ServerConfig.from_mapping(merged)
        return {"name": "Config validation", "status": "success", "detail": "Valid", "hint": ""}
    except (ValueError, TypeError) as exc:
        return {
            "name": "Config validation",
            "status": "error",
            "detail": str(exc),
            "hint": "",
        }


def _check_signage(signage: str) -> dict[str, str]:
    """Validate signage value."""
    from pounce.display import _VALID_SIGNAGE

    normalized = signage.strip().lower()
    if normalized in _VALID_SIGNAGE:
        return {"name": "Signage", "status": "success", "detail": normalized, "hint": ""}
    return {
        "name": "Signage",
        "status": "error",
        "detail": f"Invalid signage: {signage!r}",
        "hint": f"Must be one of: {', '.join(sorted(_VALID_SIGNAGE))}",
    }


# ── `pounce init` scaffold ───────────────────────────────
#
# Sprint 3: two-command zero-to-running. See docs/design/init-scope.md.


@cli.command(
    "init",
    description="Scaffold a minimal pounce project in CWD",
    display_result=False,
)
def init(directory: str | None = None, force: bool = False) -> None:
    """Write ``app.py``, ``pounce.toml``, and ``.gitignore`` into *directory*
    (current working directory by default).

    Refuses to overwrite existing scaffold files unless ``--force`` is set.
    Intended for fresh directories — real projects already have their own
    app and config.
    """
    from pounce._init import InitError, run_init

    target = Path(directory) if directory else Path.cwd()
    try:
        written = run_init(target, force=force)
    except InitError as exc:
        hint = (
            "Pass --force to overwrite, or move the existing files first."
            if exc.colliding
            else None
        )
        _die(str(exc), hint=hint)
        return

    rel = [str(p.relative_to(target)) if p.is_relative_to(target) else str(p) for p in written]
    print(f"Scaffolded {len(rel)} files in {target}:")
    for name in rel:
        print(f"  {name}")
    print("\nNext: pounce serve --app app:app")


# ── `pounce config` group ────────────────────────────────
#
# Sprint 2: config discovery without reading source. ``config schema`` emits
# a JSON Schema / TOML template from dataclasses.fields(ServerConfig);
# ``config show`` prints the resolved merged config through the Sprint 0.3
# fail-closed redaction allowlist.

config_group = cli.group("config", description="Inspect pounce configuration")


@config_group.command(
    "schema",
    description="Emit the ServerConfig schema",
    display_result=False,
)
def config_schema(output_format: str = "json") -> None:
    """Print a machine-readable description of every ServerConfig field.

    ``--output-format json`` (default) emits a JSON Schema Draft 2020-12
    document with types, defaults, and enum constraints.
    ``--output-format toml-template`` emits a commented ``pounce.toml``
    skeleton ready to uncomment and edit.
    """
    import json as _json

    from pounce._config_schema import build_schema, build_toml_template

    fmt = output_format.strip().lower()
    if fmt == "json":
        print(_json.dumps(build_schema(), indent=2, sort_keys=False, default=str))
    elif fmt == "toml-template":
        print(build_toml_template(), end="")
    else:
        _die(
            f"Unknown --output-format {output_format!r}",
            hint="Supported formats: json, toml-template.",
        )


@config_group.command(
    "show",
    description="Print the resolved merged config",
    display_result=False,
)
def config_show(
    config: str | None = None,
    output_format: str = "toml",
    host: str | None = None,
    port: int | None = None,
    workers: int | None = None,
) -> None:
    """Print the active ServerConfig through the Sprint 0.3 redaction allowlist.

    ``config show`` merges TOML, defaults, and its limited display overrides
    (``host``, ``port``, and ``workers``). It is not a full mirror of every
    ``serve`` flag.

    Secrets and filesystem paths are never printed — fields classified as
    ``REDACT_TO_BOOL`` appear as ``<name>_set = true|false``, and fields
    outside the allowlist are omitted entirely.
    """
    import json as _json

    from pounce._config_file import load_config_with_overrides
    from pounce._config_schema import _toml_value, redacted_config_view

    cli_overrides: dict[str, object] = {}
    if host is not None:
        cli_overrides["host"] = host
    if port is not None:
        cli_overrides["port"] = port
    if workers is not None:
        cli_overrides["workers"] = workers

    config_path = Path(config) if config else None
    try:
        merged = load_config_with_overrides(cli_overrides, config_path=config_path)
        server_config = ServerConfig.from_mapping(merged)
    except (ValueError, TypeError) as exc:
        _die(str(exc))
        return

    view = redacted_config_view(server_config)

    fmt = output_format.strip().lower()
    if fmt == "json":
        print(_json.dumps(view, indent=2, sort_keys=False, default=str))
    elif fmt == "toml":
        print("[pounce]")
        for key, value in view.items():
            print(f"{key} = {_toml_value(value)}")
    else:
        _die(
            f"Unknown --output-format {output_format!r}",
            hint="Supported formats: toml, json.",
        )


def main(args: list[str] | None = None) -> None:
    """Entry point for the ``pounce`` CLI command.

    Args:
        args: Command-line arguments (defaults to sys.argv[1:]).

    """
    if sys.stderr.isatty():
        try:
            from milo.version_check import check_version

            info = check_version("bengal-pounce", __version__)
            if info and info.update_available:
                from pounce._output import _render, _write

                _write(_render("version_notice.kida", current=info.current, latest=info.latest))
        except Exception:  # noqa: S110 — best-effort version notice; must never block startup
            pass

    cli.run(args)
