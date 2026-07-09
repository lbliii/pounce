#!/usr/bin/env python3
"""Read release metadata from the PEP 621 project table."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path


def project_metadata(path: Path) -> tuple[str, str]:
    """Return the validated project name and version from *path*."""
    with path.open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file).get("project")
    if not isinstance(project, dict):
        raise ValueError(f"{path} does not contain a [project] table")

    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path} [project].name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{path} [project].version must be a non-empty string")
    return name, version


def main() -> None:
    """Print one release metadata field for shell callers."""
    parser = argparse.ArgumentParser()
    parser.add_argument("field", choices=("name", "version", "title"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args()

    name, version = project_metadata(args.pyproject)
    values = {"name": name, "version": version, "title": f"{name} {version}"}
    sys.stdout.write(f"{values[args.field]}\n")


if __name__ == "__main__":
    main()
