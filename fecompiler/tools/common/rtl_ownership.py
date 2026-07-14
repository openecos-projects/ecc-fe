"""RTL source ownership classification shared by frontend flow reports."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from fecompiler.resources import frontend_repo_root
from fecompiler.utility.json import json_read


RTL_OWNERSHIPS = ("cpu", "adapter", "soc", "generated", "third_party", "tool", "unknown")


def classify_rtl_source(
    raw_path: str | Path,
    source_group: str = "",
    workspace: dict[str, Any] | None = None,
) -> str:
    path_text = str(raw_path).strip()
    if not path_text:
        return "unknown"
    path = Path(path_text).expanduser().resolve()
    group = str(source_group).strip().lower()

    if group == "soc_filelist":
        return "soc"
    if group == "generated":
        return "generated"

    repo_root = frontend_repo_root()
    if _is_relative_to(path, repo_root / "fecompiler" / "adapters"):
        return "adapter"
    if "thirdparty" in {part.lower() for part in path.parts}:
        return "third_party"
    if group in {"cpu_filelist", "input_filelist", "origin_verilog", "cpu"}:
        return "cpu"
    if _under_workspace_root(path, workspace, "soc_filelist"):
        return "soc"
    if _is_generated_path(path, workspace):
        return "generated"
    if _under_workspace_root(path, workspace, "cpu_filelist"):
        return "cpu"
    if _is_workspace_file(path, workspace, "origin_verilog"):
        return "cpu"
    return "unknown"


def rtl_source_ownership(
    workspace: dict[str, Any],
    raw_path: str | Path,
    ownership_by_path: dict[str, str] | None = None,
) -> str:
    path_text = str(raw_path).strip()
    if not path_text:
        return "unknown"
    path = Path(path_text).expanduser().resolve()
    ownership_map = ownership_by_path if ownership_by_path is not None else rtl_ownership_map(workspace)
    ownership = ownership_map.get(str(path), "")
    if ownership:
        return ownership
    return classify_rtl_source(path, workspace=workspace)


def rtl_ownership_map(workspace: dict[str, Any]) -> dict[str, str]:
    ownership_by_path: dict[str, str] = {}
    for record in prepared_rtl_sources(workspace):
        try:
            record_path = Path(str(record.get("path", ""))).expanduser().resolve()
        except (OSError, ValueError):
            continue
        ownership = str(record.get("ownership", "")).strip()
        ownership_by_path[str(record_path)] = ownership if ownership in RTL_OWNERSHIPS else "unknown"
    return ownership_by_path


def prepared_rtl_sources(workspace: dict[str, Any]) -> list[dict[str, str]]:
    manifest = str(workspace.get("prepared_manifest", "")).strip()
    if not manifest or not Path(manifest).is_file():
        return []
    data = json_read(manifest)
    records = data.get("rtl_sources", []) if isinstance(data, dict) else []
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def ownership_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(record.get("ownership", "unknown")) for record in records)
    return {ownership: counts[ownership] for ownership in RTL_OWNERSHIPS if counts[ownership]}


def _under_workspace_root(
    path: Path,
    workspace: dict[str, Any] | None,
    filelist_field: str,
) -> bool:
    if not workspace:
        return False
    filelist = str(workspace.get(filelist_field, "")).strip()
    if not filelist:
        return False
    return _is_relative_to(path, Path(filelist).expanduser().resolve().parent)


def _is_generated_path(path: Path, workspace: dict[str, Any] | None) -> bool:
    if not workspace:
        return False
    directory = str(workspace.get("directory", "")).strip()
    if not directory or not _is_relative_to(path, Path(directory).expanduser().resolve()):
        return False
    return any(part.endswith(("_fe", "_slang", "_verilator")) for part in path.parts)


def _is_workspace_file(path: Path, workspace: dict[str, Any] | None, field: str) -> bool:
    if not workspace:
        return False
    value = str(workspace.get(field, "")).strip()
    return bool(value) and path == Path(value).expanduser().resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False
