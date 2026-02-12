"""
Development error pages with rich tracebacks.

Provides beautiful, detailed error pages for debugging during development.
In production mode, returns simple 500 errors without exposing internals.

Features:
- Full traceback with source code context
- Syntax highlighting via Rosettes (if available)
- Local variables inspection
- Request details (method, path, headers)
- Safe for production (only enabled in debug mode)

Security:
- Only active when debug=True in config
- Never exposes source code or internals in production
- Sanitizes sensitive data from error output

"""

import html
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Try to import Rosettes for syntax highlighting
try:
    from rosettes import highlight_python

    _HAS_ROSETTES = True
except ImportError:
    _HAS_ROSETTES = False


def is_rosettes_available() -> bool:
    """Check if Rosettes syntax highlighter is available."""
    return _HAS_ROSETTES


def format_exception_html(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: Any,
    *,
    request_method: str = "GET",
    request_path: str = "/",
    request_headers: list[tuple[bytes, bytes]] | None = None,
) -> str:
    """Format an exception as a rich HTML error page.

    Args:
        exc_type: Exception class.
        exc_value: Exception instance.
        exc_tb: Traceback object.
        request_method: HTTP method for context.
        request_path: Request path for context.
        request_headers: Request headers for context.

    Returns:
        HTML string with rich error page.

    """
    # Extract traceback frames
    frames = _extract_frames(exc_tb)

    # Build HTML
    html_parts = [_render_header(exc_type, exc_value, request_method, request_path)]

    # Render each frame
    for frame_info in frames:
        html_parts.append(_render_frame(frame_info))

    # Render request details
    if request_headers:
        html_parts.append(_render_request_details(request_method, request_path, request_headers))

    html_parts.append(_render_footer())

    return "\n".join(html_parts)


def _extract_frames(tb: Any) -> list[dict[str, Any]]:
    """Extract frame information from a traceback.

    Args:
        tb: Traceback object.

    Returns:
        List of frame info dicts with filename, lineno, name, locals, source.

    """
    frames = []

    while tb is not None:
        frame = tb.tb_frame
        lineno = tb.tb_lineno
        filename = frame.f_code.co_filename
        name = frame.f_code.co_name

        # Read source code around the error line
        source_lines = _get_source_context(filename, lineno, context=5)

        # Get local variables (sanitized)
        local_vars = _sanitize_locals(frame.f_locals)

        frames.append({
            "filename": filename,
            "lineno": lineno,
            "name": name,
            "source": source_lines,
            "locals": local_vars,
        })

        tb = tb.tb_next

    return frames


def _get_source_context(filename: str, lineno: int, context: int = 5) -> list[tuple[int, str]]:
    """Get source code lines around the given line number.

    Args:
        filename: Path to source file.
        lineno: Line number (1-indexed).
        context: Number of lines before/after to include.

    Returns:
        List of (line_number, line_text) tuples.

    """
    try:
        with Path(filename).open(encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, ValueError):
        return []

    start = max(0, lineno - context - 1)
    end = min(len(lines), lineno + context)

    return [(i + 1, lines[i].rstrip()) for i in range(start, end)]


def _sanitize_locals(local_vars: dict[str, Any]) -> dict[str, str]:
    """Sanitize local variables for safe display.

    Removes sensitive data and formats values for display.

    Args:
        local_vars: Dictionary of local variables.

    Returns:
        Sanitized dict with string representations.

    """
    sanitized = {}
    sensitive_names = {"password", "secret", "token", "api_key", "private_key"}

    for name, value in local_vars.items():
        # Skip dunders
        if name.startswith("__") and name.endswith("__"):
            continue

        # Redact sensitive variables
        if any(sensitive in name.lower() for sensitive in sensitive_names):
            sanitized[name] = "<redacted>"
            continue

        # Format value safely
        try:
            sanitized[name] = repr(value)[:200]  # Limit length
        except Exception:
            sanitized[name] = "<unavailable>"

    return sanitized


def _render_header(
    exc_type: type[BaseException],
    exc_value: BaseException,
    method: str,
    path: str,
) -> str:
    """Render the HTML header with exception details."""
    exc_name = exc_type.__name__
    exc_msg = html.escape(str(exc_value))

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{exc_name}: {exc_msg}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            padding: 20px;
        }}
        .header {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 24px;
            margin-bottom: 20px;
        }}
        .header h1 {{
            color: #f85149;
            font-size: 28px;
            margin-bottom: 8px;
        }}
        .header .message {{
            color: #8b949e;
            font-size: 16px;
            margin-bottom: 12px;
        }}
        .header .request {{
            color: #58a6ff;
            font-size: 14px;
            font-family: monospace;
        }}
        .frame {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            margin-bottom: 16px;
            overflow: hidden;
        }}
        .frame-header {{
            background: #0d1117;
            padding: 12px 16px;
            border-bottom: 1px solid #30363d;
            font-family: monospace;
            font-size: 13px;
        }}
        .frame-header .filename {{
            color: #58a6ff;
        }}
        .frame-header .lineno {{
            color: #f85149;
            font-weight: bold;
        }}
        .source {{
            padding: 16px;
            overflow-x: auto;
        }}
        .source-line {{
            font-family: "SF Mono", Monaco, "Cascadia Code", monospace;
            font-size: 13px;
            line-height: 1.5;
            white-space: pre;
        }}
        .source-line.highlight {{
            background: #1f1f1f;
            border-left: 3px solid #f85149;
            padding-left: 13px;
            margin-left: -16px;
            padding-right: 16px;
        }}
        .line-number {{
            color: #6e7681;
            display: inline-block;
            width: 50px;
            text-align: right;
            margin-right: 16px;
            user-select: none;
        }}
        .locals {{
            background: #0d1117;
            border-top: 1px solid #30363d;
            padding: 12px 16px;
        }}
        .locals-title {{
            color: #8b949e;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 8px;
            text-transform: uppercase;
        }}
        .local-var {{
            font-family: monospace;
            font-size: 12px;
            margin-bottom: 4px;
        }}
        .local-name {{
            color: #79c0ff;
        }}
        .local-value {{
            color: #a5d6ff;
        }}
        .request-details {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 16px;
            margin-top: 20px;
        }}
        .request-details h3 {{
            color: #8b949e;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 12px;
        }}
        .request-details pre {{
            font-family: monospace;
            font-size: 12px;
            color: #a5d6ff;
            overflow-x: auto;
        }}
        .footer {{
            margin-top: 20px;
            text-align: center;
            color: #6e7681;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{exc_name}</h1>
        <div class="message">{exc_msg}</div>
        <div class="request">{method} {html.escape(path)}</div>
    </div>
"""


def _render_frame(frame_info: dict[str, Any]) -> str:
    """Render a single traceback frame as HTML."""
    filename = frame_info["filename"]
    lineno = frame_info["lineno"]
    name = frame_info["name"]
    source = frame_info["source"]
    local_vars = frame_info["locals"]

    # Render frame header
    frame_html = f"""    <div class="frame">
        <div class="frame-header">
            <span class="filename">{html.escape(filename)}</span>
            in <strong>{html.escape(name)}</strong>
            at line <span class="lineno">{lineno}</span>
        </div>
        <div class="source">
"""

    # Render source lines
    for line_num, line_text in source:
        is_error_line = line_num == lineno
        highlight_class = " highlight" if is_error_line else ""

        # Syntax highlight if Rosettes available
        if _HAS_ROSETTES and line_text.strip():
            try:
                highlighted = highlight_python(line_text, inline=True)
            except Exception:
                highlighted = html.escape(line_text)
        else:
            highlighted = html.escape(line_text)

        frame_html += f'<div class="source-line{highlight_class}"><span class="line-number">{line_num}</span>{highlighted}</div>\n'

    frame_html += "        </div>\n"

    # Render local variables if any
    if local_vars:
        frame_html += '        <div class="locals">\n'
        frame_html += '            <div class="locals-title">Local Variables</div>\n'
        for var_name, var_value in local_vars.items():
            frame_html += f'            <div class="local-var"><span class="local-name">{html.escape(var_name)}</span> = <span class="local-value">{html.escape(var_value)}</span></div>\n'
        frame_html += "        </div>\n"

    frame_html += "    </div>\n"

    return frame_html


def _render_request_details(
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
) -> str:
    """Render request details section."""
    headers_text = "\n".join(
        f"{name.decode('latin1')}: {value.decode('latin1', errors='replace')}"
        for name, value in headers
        if not any(sensitive in name.lower() for sensitive in [b"authorization", b"cookie", b"token"])
    )

    return f"""    <div class="request-details">
        <h3>Request Details</h3>
        <pre>{html.escape(headers_text)}</pre>
    </div>
"""


def _render_footer() -> str:
    """Render HTML footer."""
    return """    <div class="footer">
        Pounce Development Error Page — Not shown in production
    </div>
</body>
</html>
"""


def create_debug_error_response(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: Any,
    *,
    request_method: str = "GET",
    request_path: str = "/",
    request_headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Create an HTTP error response with rich traceback.

    Args:
        exc_type: Exception class.
        exc_value: Exception instance.
        exc_tb: Traceback object.
        request_method: HTTP method.
        request_path: Request path.
        request_headers: Request headers.

    Returns:
        Tuple of (status, headers, body) for ASGI response.

    """
    html_content = format_exception_html(
        exc_type,
        exc_value,
        exc_tb,
        request_method=request_method,
        request_path=request_path,
        request_headers=request_headers,
    )

    headers = [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"content-length", str(len(html_content)).encode("ascii")),
    ]

    return (500, headers, html_content.encode("utf-8"))


def create_production_error_response() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Create a simple 500 error response for production.

    Returns:
        Tuple of (status, headers, body) for ASGI response.

    """
    body = b"Internal Server Error"
    headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    return (500, headers, body)
