"""Application display identity for Pounce startup output."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

type SignageMode = Literal["full", "minimal", "off"]
_VALID_SIGNAGE: frozenset[str] = frozenset({"full", "minimal", "off"})


@dataclass(frozen=True, slots=True)
class DisplayConfig:
    """Application display identity for Pounce startup output."""

    name: str | None = None
    tagline: str | None = None
    version: str | None = None
    lines: tuple[str, ...] = ()
    # None = unset (merge continues to lower-priority sources); after resolve, always set.
    signage: SignageMode | None = None

    def __post_init__(self) -> None:
        if self.signage is not None and self.signage not in _VALID_SIGNAGE:
            msg = f"signage must be one of {sorted(_VALID_SIGNAGE)} (got {self.signage!r})"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CliDisplayOverrides:
    """CLI-only overrides for display resolution (highest priority)."""

    name: str | None = None
    tagline: str | None = None
    version: str | None = None
    signage: str | None = None


def _strip_str(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _parse_signage(v: str | None) -> SignageMode | None:
    s = _strip_str(v)
    if s is None:
        return None
    low = s.lower()
    if low in _VALID_SIGNAGE:
        return cast(SignageMode, low)
    return None


def _coerce_lines(v: object) -> tuple[str, ...] | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return (s,) if s else ()
    if isinstance(v, (list, tuple)):
        return tuple(s.strip() for s in v if isinstance(s, str) and s.strip())
    return None


def _dict_to_display_fields(d: dict[str, object]) -> dict[str, object]:
    """Extract known keys from a mapping; unknown keys ignored."""
    out: dict[str, object] = {}
    if "name" in d:
        out["name"] = d.get("name")
    if "tagline" in d:
        out["tagline"] = d.get("tagline")
    if "version" in d:
        out["version"] = d.get("version")
    if "lines" in d:
        out["lines"] = _coerce_lines(d.get("lines"))
    if "signage" in d:
        out["signage"] = d.get("signage")
    return out


def _merge_str(*candidates: str | None) -> str | None:
    for c in candidates:
        s = _strip_str(c)
        if s is not None:
            return s
    return None


def _merge_signage(*candidates: str | SignageMode | None) -> SignageMode:
    for c in candidates:
        if c is None:
            continue
        p = _parse_signage(str(c))
        if p is not None:
            return p
    return "full"


def _merge_lines(*candidates: tuple[str, ...] | None) -> tuple[str, ...]:
    for c in candidates:
        if c is not None and len(c) > 0:
            return c
    return ()


def _find_pyproject_path(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    cwd = Path.cwd()
    for base in (cwd, *cwd.parents):
        candidate = base / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _load_pyproject_display(path: Path) -> DisplayConfig | None:
    import tomllib

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError:
        return None
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return None
    pounce = tool.get("pounce")
    if not isinstance(pounce, dict):
        return None
    raw = pounce.get("display")
    if not isinstance(raw, dict):
        return None
    fields = _dict_to_display_fields(raw)
    try:
        return _fields_to_display_config(fields)
    except ValueError:
        return None


def _fields_to_display_config(fields: dict[str, object]) -> DisplayConfig:
    def _opt_str(key: str) -> str | None:
        if key not in fields:
            return None
        v = fields[key]
        if v is None:
            return None
        return _strip_str(str(v))

    name = _opt_str("name")
    tagline = _opt_str("tagline")
    version = _opt_str("version")
    lines: tuple[str, ...] = ()
    if "lines" in fields and fields["lines"] is not None:
        coerced = _coerce_lines(fields["lines"])
        lines = coerced if coerced is not None else ()
    sig: SignageMode | None = None
    if "signage" in fields and fields["signage"] is not None:
        p = _parse_signage(str(fields["signage"]))
        if p is not None:
            sig = p
    return DisplayConfig(name=name, tagline=tagline, version=version, lines=lines, signage=sig)


def _app_hook_display(app: object | None) -> DisplayConfig | None:
    if app is None:
        return None
    raw = getattr(app, "__pounce_display__", None)
    if raw is None:
        return None
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    fields = _dict_to_display_fields(raw)
    try:
        return _fields_to_display_config(fields)
    except ValueError:
        return None


def _validate_cli_signage(cli_signage: str | None) -> None:
    """Reject invalid explicit CLI values (other sources still ignore bad strings)."""
    if cli_signage is None:
        return
    s = _strip_str(cli_signage)
    if s is None:
        return
    if _parse_signage(s) is None:
        msg = f"signage must be one of {sorted(_VALID_SIGNAGE)} (got {cli_signage!r})"
        raise ValueError(msg)


def resolve_display_config(
    *,
    cli_name: str | None = None,
    cli_tagline: str | None = None,
    cli_version: str | None = None,
    cli_signage: str | None = None,
    config_display: DisplayConfig | None = None,
    app: object | None = None,
    pyproject_path: str | None = None,
) -> DisplayConfig:
    """Merge display settings from all sources (highest-priority wins per field).

    Priority order (highest first): CLI, environment, ``ServerConfig.display``,
    ``[tool.pounce.display]`` in discovered ``pyproject.toml``, ``app.__pounce_display__``.
    """
    _validate_cli_signage(cli_signage)

    env_name = os.environ.get("POUNCE_APP_NAME")
    env_tagline = os.environ.get("POUNCE_APP_TAGLINE")
    env_version = os.environ.get("POUNCE_APP_VERSION")
    env_signage = os.environ.get("POUNCE_SIGNAGE")
    env_pyproject = os.environ.get("POUNCE_APP_PYPROJECT")

    toml_display: DisplayConfig | None = None
    pp = _find_pyproject_path(pyproject_path or env_pyproject)
    if pp is not None:
        toml_display = _load_pyproject_display(pp)

    app_display = _app_hook_display(app)

    cfg = config_display or DisplayConfig()

    name = _merge_str(
        cli_name,
        env_name,
        cfg.name,
        toml_display.name if toml_display else None,
        app_display.name if app_display else None,
    )
    tagline = _merge_str(
        cli_tagline,
        env_tagline,
        cfg.tagline,
        toml_display.tagline if toml_display else None,
        app_display.tagline if app_display else None,
    )
    version = _merge_str(
        cli_version,
        env_version,
        cfg.version,
        toml_display.version if toml_display else None,
        app_display.version if app_display else None,
    )
    lines = _merge_lines(
        cfg.lines,
        toml_display.lines if toml_display else None,
        app_display.lines if app_display else None,
    )
    signage_merged = _merge_signage(
        cli_signage,
        env_signage,
        cfg.signage,
        toml_display.signage if toml_display else None,
        app_display.signage if app_display else None,
    )

    return DisplayConfig(
        name=name,
        tagline=tagline,
        version=version,
        lines=lines,
        signage=signage_merged,
    )
