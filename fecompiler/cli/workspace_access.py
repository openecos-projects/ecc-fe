from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fecompiler.data.workspace import load_workspace


def workspace_markers(directory: str) -> tuple[Path, Path, Path]:
    home = Path(directory).expanduser().resolve() / "home"
    return (
        home / "parameters.json",
        home / "flow.json",
        home / "home.json",
    )


def load_existing_workspace(directory: str) -> dict[str, Any] | None:
    markers = workspace_markers(directory)
    if not all(path.is_file() for path in markers):
        return None
    for path in markers:
        read_json_object(path)
    return load_workspace(directory)


def read_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value
