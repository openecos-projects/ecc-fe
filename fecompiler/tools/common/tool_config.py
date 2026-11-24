"""Read per-tool settings of a workspace.

Settings live in ``home/parameters.json`` as flat keys (``lint_waivers``,
``elab_extra_args``); the loaded workspace record carries only some of them.
Steps and provenance both read them through here, so they agree on the value.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_PARAMETER_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*=.+")


def tool_config(workspace: dict[str, Any]) -> dict[str, Any]:
    """Return the stored parameters overlaid with the loaded workspace record."""
    merged: dict[str, Any] = {}
    path = str(workspace.get("parameters_path", "")).strip()
    if path:
        try:
            with open(path, encoding="utf-8") as stream:
                stored = json.load(stream)
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            merged.update(stored)
    merged.update(workspace)
    return merged


def config_text(workspace: dict[str, Any], key: str, default: str = "") -> str:
    value = tool_config(workspace).get(key)
    return str(value).strip() if value not in (None, "") else default


def config_list(workspace: dict[str, Any], key: str) -> list[str]:
    """Return a list setting; a JSON array stored as text is accepted too."""
    value = tool_config(workspace).get(key)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except ValueError:
            value = [text]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def config_base_dir(workspace: dict[str, Any]) -> Path:
    """Return the directory relative setting paths are resolved against.

    A project run resolves against the project directory, where
    ``ecc-fe.toml`` is; a direct workspace against the workspace itself.
    """
    directory = Path(str(workspace.get("directory", "") or ".")).expanduser().resolve()
    if directory.parent.name == "runs":
        project = directory.parent.parent
        if (project / "ecc-fe.toml").is_file():
            return project
    if directory.name == "template" and directory.parent.name == ".ecc-fe":
        return directory.parent.parent
    return directory


def resolve_config_path(workspace: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else config_base_dir(workspace) / path).resolve()


def config_paths(workspace: dict[str, Any], key: str) -> list[Path]:
    """Return the files a path setting (one path or a list of paths) names."""
    value = tool_config(workspace).get(key)
    names = config_list(workspace, key) if not isinstance(value, str) or value.strip().startswith("[") else [value.strip()]
    return [resolve_config_path(workspace, name) for name in names if name]


def design_parameter_overrides(workspace: dict[str, Any]) -> list[str]:
    """Return ``NAME=value`` top-level parameter overrides, validated."""
    overrides = config_list(workspace, "design_parameters")
    invalid = [item for item in overrides if not _PARAMETER_ASSIGNMENT.fullmatch(item)]
    if invalid:
        raise ValueError(
            "design.parameters entries must look like NAME=value: " + ", ".join(invalid)
        )
    return overrides
