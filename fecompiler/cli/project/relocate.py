"""Keep a project usable after its directory has been moved or copied.

A project records absolute paths: where each workspace lives, and where its
inputs are. Paths below the project directory move with it, so when the
directory is found somewhere else they are rewritten from the old location to
the new one. Inputs outside the project are left alone.

The location is recorded in ``.ecc-fe/location.json``. A project created
before that file existed is given one on first use, with no rewrite: it is
assumed to still be where it was created unless its template says otherwise.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fecompiler.cli.project.layout import (
    TEMPLATE_RELATIVE,
    _rewrite_home_paths,
    _rewrite_origin_paths,
    is_valid_run_id,
)
from fecompiler.utility.atomic_write import atomic_write_json
from fecompiler.utility.workspace_lock import WorkspaceBusyError, workspace_lock

LOCATION_RELATIVE = Path(".ecc-fe") / "location.json"


def record_project_location(project_dir: str) -> None:
    """Remember where the project is; called when it is created."""
    _write_location(Path(project_dir).resolve())


def relocate_project(project_dir: str) -> dict[str, object] | None:
    """Rewrite the project's own paths if it is not where it was last used.

    Returns ``{"from", "to", "workspaces"}`` when something was rewritten and
    ``None`` otherwise. Never raises: a project that cannot be updated is left
    as it is and behaves as before.
    """
    project = Path(project_dir).resolve()
    template = project / TEMPLATE_RELATIVE
    if not template.is_dir():
        return None
    try:
        previous = _recorded_location(project) or _template_location(template)
        if previous is None or previous == project:
            if not (project / LOCATION_RELATIVE).is_file():
                _write_location(project)
            return None
        rewritten = [
            str(workspace)
            for workspace in _project_workspaces(project)
            if _rewrite_workspace(workspace, previous, project)
        ]
        _write_location(project)
    except (OSError, ValueError, TypeError):
        return None
    return {"from": str(previous), "to": str(project), "workspaces": rewritten}


def relocate_workspace(workspace_dir: str) -> dict[str, object] | None:
    """Rewrite a direct workspace's own paths if its directory was moved.

    A direct workspace keeps no location record: where it was is read from
    the path it stores to its own parameters file. Returns ``{"from", "to",
    "workspaces"}`` when something was rewritten. Never raises.
    """
    workspace = Path(workspace_dir).resolve()
    try:
        previous = _workspace_location(workspace)
        if previous is None or previous == workspace:
            return None
        if not _rewrite_workspace(workspace, previous, workspace):
            return None
    except (OSError, ValueError, TypeError):
        return None
    return {"from": str(previous), "to": str(workspace), "workspaces": [str(workspace)]}


def _workspace_location(workspace: Path) -> Path | None:
    """Derive the directory a workspace's stored paths were written for."""
    try:
        home = json.loads((workspace / "home" / "home.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    recorded = home.get("parameters") if isinstance(home, dict) else None
    suffix = os.sep + str(Path("home") / "parameters.json")
    if isinstance(recorded, str) and recorded.endswith(suffix):
        return Path(recorded[: -len(suffix)])
    return None


def _write_location(project: Path) -> None:
    path = project / LOCATION_RELATIVE
    if path.parent.is_dir():
        atomic_write_json(path, {"project_dir": str(project)})


def _recorded_location(project: Path) -> Path | None:
    try:
        payload = json.loads((project / LOCATION_RELATIVE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = payload.get("project_dir") if isinstance(payload, dict) else None
    return Path(value) if isinstance(value, str) and value else None


def _template_location(template: Path) -> Path | None:
    """Derive the project directory the template was written for."""
    try:
        home = json.loads((template / "home" / "home.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    recorded = home.get("parameters") if isinstance(home, dict) else None
    suffix = os.sep + str(TEMPLATE_RELATIVE / "home" / "parameters.json")
    if isinstance(recorded, str) and recorded.endswith(suffix):
        return Path(recorded[: -len(suffix)])
    return None


def _project_workspaces(project: Path) -> list[Path]:
    workspaces = [project / TEMPLATE_RELATIVE]
    runs = project / "runs"
    if runs.is_dir():
        workspaces.extend(
            path
            for path in sorted(runs.iterdir())
            if is_valid_run_id(path.name) and path.is_dir() and not path.is_symlink()
        )
    return workspaces


def _rewrite_workspace(workspace: Path, previous: Path, project: Path) -> bool:
    home = workspace / "home"
    if not home.is_dir():
        return False
    try:
        with workspace_lock(workspace):
            _rewrite_home_paths(home, previous, project)
            origin = workspace / "origin"
            if origin.is_dir():
                _rewrite_origin_paths(origin, previous, project)
    except WorkspaceBusyError:
        return False
    return True
