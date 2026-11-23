from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from fecompiler.cli.workspace_access import load_existing_workspace, workspace_markers

TEMPLATE_RELATIVE = Path(".ecc-fe") / "template"
_STAGING_MARK = ".staging-"
_RETIRED_MARK = ".retired-"


def resolve_run_dir(
    project_dir: str, run_id: str | None = None
) -> tuple[str, str | None]:
    project = Path(project_dir)
    if run_id is None:
        return str(project / "runs" / "default"), None
    if not run_id:
        return str(project / "runs" / "default"), run_id
    if run_id == "default":
        return str(project / "runs" / "default"), "default"
    candidate = Path(run_id).expanduser()
    if candidate.is_absolute():
        return str(candidate), run_id
    if "/" in run_id or os.sep in run_id:
        return str(project / candidate), run_id
    return str(project / "runs" / run_id), run_id


def template_dir(project_dir: str) -> str:
    return str(Path(project_dir) / TEMPLATE_RELATIVE)


def is_workspace(directory: str) -> bool:
    return all(path.is_file() for path in workspace_markers(directory))


def clone_workspace_template(source: str, destination: str) -> None:
    destination_path = Path(destination).expanduser().resolve()
    _clone_template(source, destination_path, published_path=destination_path)


def stage_workspace_replacement(source: str, destination: str) -> str:
    """Build the replacement for an existing run beside it, leaving it intact.

    The staged workspace already records *destination* as its own location, so
    :func:`publish_workspace_replacement` only has to rename it into place.
    """
    destination_path = Path(destination).expanduser().resolve()
    for leftover in _replacement_leftovers(destination_path):
        shutil.rmtree(leftover, ignore_errors=True)
    staging = destination_path.with_name(
        f".{destination_path.name}{_STAGING_MARK}{uuid.uuid4().hex[:8]}"
    )
    _clone_template(source, staging, published_path=destination_path)
    return str(staging)


def publish_workspace_replacement(staging: str, destination: str) -> None:
    """Swap a staged workspace in for the run it replaces.

    The previous run is deleted only after the new one is in place, and is
    restored if the swap fails.
    """
    destination_path = Path(destination).expanduser().resolve()
    retired = destination_path.with_name(
        f".{destination_path.name}{_RETIRED_MARK}{uuid.uuid4().hex[:8]}"
    )
    os.rename(destination_path, retired)
    try:
        os.rename(staging, destination_path)
    except BaseException:
        os.rename(retired, destination_path)
        raise
    shutil.rmtree(retired, ignore_errors=True)


def _replacement_leftovers(destination: Path) -> list[Path]:
    """Staging and retired directories abandoned by an interrupted overwrite."""
    pattern = re.compile(
        rf"\.{re.escape(destination.name)}"
        rf"(?:{re.escape(_STAGING_MARK)}|{re.escape(_RETIRED_MARK)})[0-9a-f]{{8}}"
    )
    return [
        path
        for path in destination.parent.iterdir()
        if pattern.fullmatch(path.name) and path.is_dir() and not path.is_symlink()
    ]


def _clone_template(source: str, destination: Path, *, published_path: Path) -> None:
    source_path = Path(source).expanduser().resolve()
    if load_existing_workspace(str(source_path)) is None:
        raise ValueError(f"Project workspace template is invalid: {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        for name in ("origin", "home"):
            shutil.copytree(source_path / name, destination / name)
        _rewrite_origin_paths(destination / "origin", source_path, published_path)
        _rewrite_home_paths(destination / "home", source_path, published_path)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def is_safe_run_directory(path: str) -> bool:
    target = Path(path)
    if not target.is_dir() or target.is_symlink():
        return False
    try:
        if not any(target.iterdir()):
            return True
    except OSError:
        return False
    home = target / "home"
    if home.is_symlink():
        return False
    return all(
        not marker.is_symlink() and marker.is_file()
        for marker in workspace_markers(path)
    )


def resolves_as_spelled(path: str, project_dir: str) -> bool:
    spelled = os.path.normpath(path)
    anchor = os.path.normpath(project_dir)
    if spelled == anchor:
        return os.path.realpath(path) == os.path.realpath(anchor)
    if spelled.startswith(anchor + os.sep):
        tail = spelled[len(anchor) + 1 :]
        return os.path.realpath(path) == os.path.join(os.path.realpath(anchor), tail)
    return os.path.realpath(path) == spelled


def is_protected_run_target(path: str, project_dir: str) -> bool:
    spelled = Path(os.path.abspath(os.path.normpath(path)))
    project = Path(os.path.abspath(os.path.normpath(project_dir)))
    runs = project / "runs"
    template = project / TEMPLATE_RELATIVE

    real = Path(os.path.realpath(path))
    real_project = Path(os.path.realpath(project_dir))
    real_runs = real_project / "runs"
    real_template = real_project / TEMPLATE_RELATIVE

    return (
        _contains(spelled, project)
        or spelled == runs
        or _contains(spelled, template)
        or _contains(template, spelled)
        or _contains(real, real_project)
        or real == real_runs
        or _contains(real, real_template)
        or _contains(real_template, real)
    )


def _contains(directory: Path, candidate: Path) -> bool:
    return candidate == directory or candidate.is_relative_to(directory)


def _rewrite_home_paths(home: Path, source: Path, destination: Path) -> None:
    for path in home.glob("*.json"):
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        rewritten = _replace_path_prefix(payload, str(source), str(destination))
        path.write_text(
            json.dumps(rewritten, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _rewrite_origin_paths(origin: Path, source: Path, destination: Path) -> None:
    for path in origin.iterdir():
        if not path.is_file() or path.suffix.lower() not in {
            ".f",
            ".fl",
            ".filelist",
        }:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        path.write_text(
            content.replace(str(source) + os.sep, str(destination) + os.sep),
            encoding="utf-8",
        )


def _replace_path_prefix(value: object, source: str, destination: str) -> object:
    if isinstance(value, str):
        if value == source:
            return destination
        if value.startswith(source + os.sep):
            return destination + value[len(source) :]
        return value
    if isinstance(value, list):
        return [_replace_path_prefix(item, source, destination) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_path_prefix(item, source, destination)
            for key, item in value.items()
        }
    return value
