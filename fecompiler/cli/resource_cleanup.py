"""Remove what the resource manager no longer needs: one resource, or leftovers.

Installing a newer version leaves the older one's directory in place, an
install that is killed leaves its staging directories, and a download that is
given up leaves its partial file. Nothing here touches what the manifest says
is installed, and nothing outside the managed tools directory and the
download cache.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from fecompiler.resources import installed_tool_entries, read_resource_manifest
from fecompiler.utility.atomic_write import atomic_write_json

# A staging directory or partial download younger than this may belong to an
# install that is running right now.
RECENT_SECONDS = 3600.0
_STAGING_PREFIXES = (".extract-", ".backup-")
_DOWNLOAD_SUFFIXES = (".archive", ".part", ".download")


class CleanupError(RuntimeError):
    pass


def directory_bytes(path: Path) -> int:
    """Return the size of a file or of everything below a directory."""
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for root, _directories, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return path.resolve() != directory.resolve()


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _age_seconds(path: Path, now: float) -> float:
    try:
        return now - path.lstat().st_mtime
    except OSError:
        return 0.0


def prune_candidates(
    *, manifest_path: Path, tools_dir: Path, cache_dir: Path, now: float | None = None
) -> list[dict[str, Any]]:
    """Name what can be removed without changing what is installed.

    Each candidate has ``category`` (``old_version``, ``staging``,
    ``download``), ``path``, ``bytes`` and ``recent``: True when it is young
    enough to belong to an install that is running.
    """
    moment = time.time() if now is None else now
    installed = installed_tool_entries(read_resource_manifest(manifest_path, strict=True))
    candidates: list[dict[str, Any]] = []

    def add(category: str, path: Path, **fields: object) -> None:
        candidates.append(
            {
                "category": category,
                "path": str(path),
                "bytes": directory_bytes(path),
                "recent": _age_seconds(path, moment) < RECENT_SECONDS,
                **fields,
            }
        )

    for tool_dir in sorted(tools_dir.iterdir()) if tools_dir.is_dir() else []:
        if not tool_dir.is_dir() or tool_dir.is_symlink():
            continue
        children = sorted(tool_dir.iterdir())
        for child in children:
            if child.name.startswith(_STAGING_PREFIXES):
                add("staging", child, resource_id=f"tool:{tool_dir.name}")
        entry = installed.get(tool_dir.name)
        # Versions are judged only where the installed one is known and is
        # one of this directory's own.
        if entry is None or entry.get("managed", True) is False:
            continue
        active = Path(str(entry.get("path", ""))).expanduser()
        if not _inside(active, tool_dir) or not active.is_dir():
            continue
        for child in children:
            if (
                child.is_dir()
                and not child.is_symlink()
                and not child.name.startswith(".")
                and child.resolve() != active.resolve()
            ):
                add(
                    "old_version",
                    child,
                    resource_id=f"tool:{tool_dir.name}",
                    version=child.name,
                    installed_version=entry.get("version"),
                )
    for item in sorted(cache_dir.iterdir()) if cache_dir.is_dir() else []:
        if item.is_file() and item.name.endswith(_DOWNLOAD_SUFFIXES):
            add("download", item)
    return candidates


def prune(
    *,
    manifest_path: Path,
    tools_dir: Path,
    cache_dir: Path,
    apply: bool,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Report the candidates, and remove them when *apply* is set.

    An old version is removed whatever its age; staging directories and
    downloads that are recent are kept, since an install may be using them.
    """
    records: list[dict[str, Any]] = []
    for candidate in prune_candidates(
        manifest_path=manifest_path, tools_dir=tools_dir, cache_dir=cache_dir, now=now
    ):
        path = Path(candidate["path"])
        kept_recent = candidate["recent"] and candidate["category"] != "old_version"
        if kept_recent:
            status = "kept_recent"
        elif not apply:
            status = "would_remove"
        else:
            try:
                _remove(path)
                status = "removed"
            except OSError as error:
                status = "failed"
                candidate["message"] = str(error)
        records.append({"kind": "resource_prune", "status": status, **candidate})
    return records


def uninstall(
    name: str, *, manifest_path: Path, tools_dir: Path, lock: Any
) -> dict[str, Any]:
    """Remove one installed resource: its manifest entry, then its directory.

    The directory is removed only when the resource manager put it there:
    the entry is managed and its path is below the tools directory.
    Otherwise the entry alone is dropped and the files are left.
    """
    with lock(manifest_path):
        manifest = read_resource_manifest(manifest_path, strict=True)
        installed = manifest.get("installed", {})
        key = next(
            (
                resource_id
                for resource_id, entry in installed.items()
                if isinstance(entry, dict)
                and entry.get("type") == "tool"
                and str(entry.get("name") or str(resource_id).removeprefix("tool:")) == name
            ),
            None,
        )
        if key is None:
            raise CleanupError(f"Resource tool:{name} is not installed")
        entry = installed[key]
        path = Path(str(entry.get("path", ""))).expanduser()
        owned = (
            entry.get("managed", True) is not False
            and bool(str(entry.get("path", "")).strip())
            and _inside(path, tools_dir / name)
        )
        required = f"tool:{name}" in {
            str(item) if str(item).startswith("tool:") else f"tool:{item}"
            for item in manifest.get("required_resources", []) or []
        }
        # The entry goes first: a directory that is half removed must not be
        # one the manifest still calls installed.
        del installed[key]
        atomic_write_json(manifest_path, manifest)
    record: dict[str, Any] = {
        "kind": "resource_uninstall",
        "resource_id": f"tool:{name}",
        "version": entry.get("version"),
        "path": str(path) if str(entry.get("path", "")).strip() else None,
        "required": required,
    }
    if not owned:
        record.update(status="unregistered", files="kept", reason="not installed by the resource manager")
        return record
    record["bytes"] = directory_bytes(path) if path.exists() else 0
    try:
        if path.exists():
            _remove(path)
        parent = tools_dir / name
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as error:
        record.update(status="unregistered", files="partly_removed", message=str(error))
        return record
    record.update(status="uninstalled", files="removed")
    return record


def recover_staging(tool_dir: Path, *, installed_path: Path | None, now: float | None = None) -> list[str]:
    """Put right what an install of this tool left behind when it was killed.

    A backup made while replacing the installed version is put back when the
    installed directory is gone; staging directories that are no longer
    recent are removed. Returns what was done, for the log.
    """
    moment = time.time() if now is None else now
    actions: list[str] = []
    if not tool_dir.is_dir():
        return actions
    for child in sorted(tool_dir.iterdir()):
        if not child.name.startswith(_STAGING_PREFIXES) or not child.is_dir():
            continue
        try:
            if (
                child.name.startswith(".backup-")
                and installed_path is not None
                and installed_path.parent == tool_dir
                and child.name.startswith(f".backup-{installed_path.name}-")
                and not installed_path.exists()
            ):
                # Killed between moving the old version away and the new one in.
                os.replace(child, installed_path)
                actions.append(f"restored {installed_path} from {child.name}")
            elif _age_seconds(child, moment) >= RECENT_SECONDS:
                shutil.rmtree(child)
                actions.append(f"removed {child.name}")
        except OSError:
            continue
    return actions


__all__ = [
    "CleanupError",
    "RECENT_SECONDS",
    "directory_bytes",
    "prune",
    "prune_candidates",
    "recover_staging",
    "uninstall",
]
