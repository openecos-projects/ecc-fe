from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fecompiler.resources import (
    MANIFEST_SCHEMA_VERSION,
    current_registry_platform,
    empty_resource_manifest,
    installed_tool_entries,
    managed_tools_dir,
    read_resource_manifest,
    resource_manifest_path,
    tool_entry_health,
    xdg_cache_home,
)

DEFAULT_REGISTRY_URL = "https://emin017.github.io/ecos-registry/tool-registry.json"
ALL_PLATFORM = "all-platform"
EXECUTABLE_TOOLS = frozenset(
    {"ecc-fe", "slang", "verilator", "yosys", "riscv-toolchain"}
)


class ResourceManagerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RegistryAsset:
    url: str
    sha256: str
    size: int | None
    strip_prefix: str | None
    metadata_url: str | None
    supplemental_assets: tuple[dict[str, Any], ...]
    post_install: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class RegistryVersion:
    version: str
    requires: tuple[str, ...]
    asset: RegistryAsset
    platform: str


ProgressCallback = Callable[[dict[str, object]], None]


class ResourceManager:
    def __init__(
        self,
        *,
        registry_url: str | None = None,
        manifest_path: Path | None = None,
        tools_dir: Path | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.registry_url = (
            registry_url or os.getenv("ECOS_REGISTRY_URL") or DEFAULT_REGISTRY_URL
        )
        self.manifest_path = manifest_path or resource_manifest_path()
        self.tools_dir = tools_dir or managed_tools_dir()
        self.cache_dir = cache_dir or xdg_cache_home() / "ecos-studio" / "downloads"

    def registry(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(self.registry_url, timeout=30) as response:
                value = json.load(response)
        except (OSError, ValueError) as error:
            raise ResourceManagerError(
                f"Unable to read resource registry {self.registry_url}: {error}"
            ) from error
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise ResourceManagerError(
                "Unsupported or invalid resource registry schema"
            )
        if not isinstance(value.get("tools"), list):
            raise ResourceManagerError("Resource registry tools field must be an array")
        return value

    def list_resources(
        self, *, registry: dict[str, Any] | None = None
    ) -> list[dict[str, object]]:
        source = registry or self.registry()
        manifest = read_resource_manifest(self.manifest_path, strict=True)
        installed = installed_tool_entries(manifest)
        records: list[dict[str, object]] = []
        for tool in source.get("tools", []):
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", ""))
            entry = installed.get(name)
            availability_error: str | None = None
            try:
                latest = self.resolve_version(source, name) if name else None
                if latest and latest.asset.metadata_url:
                    latest = RegistryVersion(
                        version=latest.version,
                        requires=latest.requires,
                        asset=self._resolve_metadata(latest.asset),
                        platform=latest.platform,
                    )
            except ResourceManagerError as error:
                latest = None
                availability_error = str(error)
            health = tool_entry_health(entry) if entry else {"status": "missing"}
            update_available = bool(
                entry
                and latest
                and (
                    str(entry.get("version", "")) != latest.version
                    or str(entry.get("sha256", "")).lower()
                    != latest.asset.sha256.lower()
                )
            )
            records.append(
                {
                    "kind": "resource",
                    "resource_id": f"tool:{name}",
                    "name": name,
                    "display_name": str(tool.get("display_name") or name),
                    "status": (
                        "update_available"
                        if update_available and health.get("status") == "ok"
                        else "installed"
                        if health.get("status") == "ok"
                        else "unavailable"
                        if availability_error
                        else str(health.get("status", "missing"))
                    ),
                    "installed_version": entry.get("version") if entry else None,
                    "available_version": latest.version if latest else None,
                    "platform": latest.platform
                    if latest
                    else current_registry_platform(),
                    "path": entry.get("path") if entry else None,
                    "requires": list(latest.requires) if latest else [],
                    "missing_markers": health.get("missing_markers", []),
                    "availability_error": availability_error,
                }
            )
        return records

    def required_resources(
        self, *, registry: dict[str, Any] | None = None
    ) -> tuple[str, ...]:
        source = registry or self.registry()
        return self.resolve_version(source, "ecc-fe").requires

    def install_required(
        self, *, progress: ProgressCallback | None = None
    ) -> list[dict[str, object]]:
        registry = self.registry()
        required = self.required_resources(registry=registry)
        self._record_required_resources(required)
        records: list[dict[str, object]] = []
        for resource_id in required:
            records.extend(
                self.install(resource_id, registry=registry, progress=progress)
            )
        return _dedupe_install_records(records)

    def install(
        self,
        resource_id: str,
        *,
        version: str | None = None,
        registry: dict[str, Any] | None = None,
        progress: ProgressCallback | None = None,
        _visiting: set[str] | None = None,
    ) -> list[dict[str, object]]:
        source = registry or self.registry()
        normalized = _normalize_resource_id(resource_id)
        name = normalized.removeprefix("tool:")
        visiting = _visiting if _visiting is not None else set()
        if normalized in visiting:
            chain = " -> ".join([*sorted(visiting), normalized])
            raise ResourceManagerError(f"Resource dependency cycle detected: {chain}")
        visiting.add(normalized)
        selected = self.resolve_version(source, name, version)
        records: list[dict[str, object]] = []
        try:
            for dependency in selected.requires:
                records.extend(
                    self.install(
                        dependency,
                        registry=source,
                        progress=progress,
                        _visiting=visiting,
                    )
                )
            records.append(self._install_one(name, selected, progress=progress))
        finally:
            visiting.remove(normalized)
        return _dedupe_install_records(records)

    def resolve_version(
        self,
        registry: dict[str, Any],
        name: str,
        requested_version: str | None = None,
    ) -> RegistryVersion:
        tool = next(
            (
                item
                for item in registry.get("tools", [])
                if isinstance(item, dict) and item.get("name") == name
            ),
            None,
        )
        if tool is None:
            raise ResourceManagerError(f"Tool '{name}' was not found in the registry")
        versions = tool.get("versions", [])
        if not isinstance(versions, list) or not versions:
            raise ResourceManagerError(f"Tool '{name}' has no available versions")
        raw_version = next(
            (
                item
                for item in versions
                if isinstance(item, dict)
                and (
                    requested_version is None
                    or item.get("version") == requested_version
                )
            ),
            None,
        )
        if raw_version is None:
            raise ResourceManagerError(
                f"Version '{requested_version}' was not found for {name}"
            )
        platforms = raw_version.get("platforms", {})
        platform_name = current_registry_platform()
        if not isinstance(platforms, dict):
            raise ResourceManagerError(f"Tool '{name}' has invalid platform assets")
        raw_asset = platforms.get(platform_name) or platforms.get(ALL_PLATFORM)
        if not isinstance(raw_asset, dict):
            raise ResourceManagerError(
                f"Tool '{name}' is unavailable on {platform_name}"
            )
        asset = RegistryAsset(
            url=str(raw_asset.get("url", "")),
            sha256=str(raw_asset.get("sha256", "")).lower(),
            size=_positive_int(raw_asset.get("size")),
            strip_prefix=str(raw_asset.get("strip_prefix") or "") or None,
            metadata_url=str(raw_asset.get("metadata_url") or "") or None,
            supplemental_assets=tuple(
                item
                for item in raw_asset.get("supplemental_assets", [])
                if isinstance(item, dict)
            ),
            post_install=tuple(
                item
                for item in raw_asset.get("post_install", [])
                if isinstance(item, dict)
            ),
        )
        if not asset.url or not _valid_sha256(asset.sha256) or asset.size is None:
            raise ResourceManagerError(
                f"Tool '{name}' is missing its URL, SHA256 checksum, or size lock"
            )
        requirements = tuple(
            _normalize_resource_id(str(item))
            for item in raw_version.get("requires", [])
            if str(item).strip()
        )
        return RegistryVersion(
            version=str(raw_version.get("version", "")),
            requires=requirements,
            asset=asset,
            platform=platform_name if platform_name in platforms else ALL_PLATFORM,
        )

    def _install_one(
        self,
        name: str,
        selected: RegistryVersion,
        *,
        progress: ProgressCallback | None,
    ) -> dict[str, object]:
        asset = self._resolve_metadata(selected.asset)
        manifest = read_resource_manifest(self.manifest_path, strict=True)
        existing = installed_tool_entries(manifest).get(name)
        if (
            existing
            and str(existing.get("version")) == selected.version
            and str(existing.get("sha256", "")).lower() == asset.sha256
            and tool_entry_health(existing)["status"] == "ok"
        ):
            return _install_record(
                name, selected.version, "already_installed", existing.get("path")
            )

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        archive_path = (
            self.cache_dir / f"{name}-{selected.version}-{uuid.uuid4().hex}.download"
        )
        extract_parent = self.tools_dir / name
        extract_parent.mkdir(parents=True, exist_ok=True)
        stage_path = extract_parent / f".extract-{selected.version}-{uuid.uuid4().hex}"
        destination = extract_parent / selected.version
        backup_path = extract_parent / f".backup-{selected.version}-{uuid.uuid4().hex}"
        _publish(progress, name, "downloading", 0.0)
        try:
            self._download(
                asset.url,
                archive_path,
                expected_size=asset.size,
                progress=progress,
                resource_name=name,
            )
            _publish(progress, name, "verifying", 0.0)
            _verify_sha256(archive_path, asset.sha256)
            stage_path.mkdir(parents=True)
            _extract_archive(archive_path, stage_path)
            payload = _payload_root(stage_path, asset.strip_prefix)
            self._install_supplemental_assets(payload, asset.supplemental_assets)
            self._run_post_install(payload, asset.post_install)
            entry = self._build_entry(name, selected.version, asset, payload)
            if name in EXECUTABLE_TOOLS and not entry["executable"]:
                raise ResourceManagerError(
                    f"Installed resource {name} does not contain an executable"
                )
            health = tool_entry_health(entry)
            if health["status"] != "ok":
                missing = ", ".join(str(item) for item in health["missing_markers"])
                raise ResourceManagerError(
                    f"Health check failed for {name}: missing {missing}"
                )

            if destination.exists():
                os.replace(destination, backup_path)
            try:
                os.replace(payload, destination)
            except Exception:
                if backup_path.exists() and not destination.exists():
                    os.replace(backup_path, destination)
                raise
            entry["path"] = str(destination.resolve())
            entry["detected_executables"] = _detect_executables(destination)
            entry["executable"] = _preferred_executable(
                name, entry["detected_executables"]
            )
            try:
                self._record_install(name, entry)
            except Exception:
                if destination.exists():
                    shutil.rmtree(destination)
                if backup_path.exists():
                    os.replace(backup_path, destination)
                raise
            if backup_path.exists():
                shutil.rmtree(backup_path)
            _publish(progress, name, "done", 1.0)
            return _install_record(name, selected.version, "installed", destination)
        finally:
            archive_path.unlink(missing_ok=True)
            if stage_path.exists():
                shutil.rmtree(stage_path)
            if backup_path.exists():
                if destination.exists():
                    shutil.rmtree(backup_path)
                else:
                    os.replace(backup_path, destination)

    def _resolve_metadata(self, asset: RegistryAsset) -> RegistryAsset:
        if not asset.metadata_url:
            return asset
        try:
            with urllib.request.urlopen(asset.metadata_url, timeout=30) as response:
                metadata = json.load(response)
        except (OSError, ValueError) as error:
            raise ResourceManagerError(
                f"Unable to read release metadata: {error}"
            ) from error
        if not isinstance(metadata, dict):
            raise ResourceManagerError("Release metadata must be an object")
        sha256 = str(metadata.get("sha256", "")).lower()
        size = _positive_int(metadata.get("size"))
        if not _valid_sha256(sha256) or size is None:
            raise ResourceManagerError("Release metadata is missing SHA256 or size")
        if sha256 != asset.sha256 or size != asset.size:
            raise ResourceManagerError(
                "Release metadata does not match the registry SHA256 and size lock"
            )
        return asset

    def _download(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int | None,
        progress: ProgressCallback | None = None,
        resource_name: str = "",
    ) -> None:
        received = 0
        last_reported = 0
        try:
            with (
                urllib.request.urlopen(url, timeout=60) as response,
                destination.open("wb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    received += len(chunk)
                    if progress and resource_name and expected_size:
                        percent = min(100, received * 100 // expected_size)
                        if percent >= last_reported + 10 or percent == 100:
                            last_reported = percent
                            _publish(
                                progress, resource_name, "downloading", percent / 100
                            )
        except OSError as error:
            raise ResourceManagerError(f"Download failed for {url}: {error}") from error
        if expected_size is not None and received != expected_size:
            raise ResourceManagerError(
                f"Download size mismatch for {url}: expected {expected_size}, received {received}"
            )

    def _install_supplemental_assets(
        self,
        root: Path,
        assets: tuple[dict[str, Any], ...],
    ) -> None:
        for item in assets:
            relative = _safe_relative_path(str(item.get("path", "")))
            url = str(item.get("url", ""))
            sha256 = str(item.get("sha256", "")).lower()
            size = _positive_int(item.get("size"))
            if not relative.parts or not url or not sha256 or size is None:
                raise ResourceManagerError("Invalid supplemental asset declaration")
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._download(url, destination, expected_size=size)
            _verify_sha256(destination, sha256)

    def _run_post_install(self, root: Path, steps: tuple[dict[str, Any], ...]) -> None:
        for step in steps:
            command = step.get("command", [])
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(x, str) for x in command)
            ):
                raise ResourceManagerError("Invalid post-install command")
            relative = _safe_relative_path(str(step.get("cwd") or "."))
            cwd = root.joinpath(*relative.parts).resolve()
            if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
                raise ResourceManagerError(
                    "Post-install working directory is outside the resource"
                )
            try:
                subprocess.run(command, cwd=cwd, check=True)
            except (OSError, subprocess.CalledProcessError) as error:
                raise ResourceManagerError(
                    f"Post-install command failed: {' '.join(command)}"
                ) from error

    def _build_entry(
        self,
        name: str,
        version: str,
        asset: RegistryAsset,
        root: Path,
    ) -> dict[str, Any]:
        executables = _detect_executables(root)
        return {
            "type": "tool",
            "name": name,
            "version": version,
            "path": str(root.resolve()),
            "installed_at": datetime.now(UTC).isoformat(),
            "sha256": asset.sha256,
            "size": asset.size,
            "detected_executables": executables,
            "executable": _preferred_executable(name, executables),
            "active": True,
            "managed": True,
        }

    def _record_install(self, name: str, entry: dict[str, Any]) -> None:
        with _manifest_lock(self.manifest_path):
            manifest = read_resource_manifest(self.manifest_path, strict=True)
            manifest["schema_version"] = max(
                int(manifest.get("schema_version", 1)), MANIFEST_SCHEMA_VERSION
            )
            defaults = empty_resource_manifest()
            manifest["resources_dir"] = str(self.manifest_path.parent)
            manifest["tools_dir"] = str(self.tools_dir)
            manifest.setdefault("pdks_dir", defaults["pdks_dir"])
            manifest.setdefault("mpcs_dir", defaults["mpcs_dir"])
            manifest.setdefault("required_resources", [])
            manifest.setdefault("pdk_references", [])
            installed = manifest.setdefault("installed", {})
            if not isinstance(installed, dict):
                raise ResourceManagerError(
                    "Resource manifest installed field must be an object"
                )
            installed[f"tool:{name}"] = entry
            _atomic_write_json(self.manifest_path, manifest)

    def _record_required_resources(self, resource_ids: tuple[str, ...]) -> None:
        with _manifest_lock(self.manifest_path):
            manifest = read_resource_manifest(self.manifest_path, strict=True)
            manifest["schema_version"] = max(
                int(manifest.get("schema_version", 1)), MANIFEST_SCHEMA_VERSION
            )
            defaults = empty_resource_manifest()
            manifest["resources_dir"] = str(self.manifest_path.parent)
            manifest["tools_dir"] = str(self.tools_dir)
            manifest.setdefault("pdks_dir", defaults["pdks_dir"])
            manifest.setdefault("mpcs_dir", defaults["mpcs_dir"])
            manifest.setdefault("pdk_references", [])
            manifest.setdefault("installed", {})
            manifest["required_resources"] = list(dict.fromkeys(resource_ids))
            _atomic_write_json(self.manifest_path, manifest)


def _normalize_resource_id(resource_id: str) -> str:
    value = resource_id.strip()
    if not value:
        raise ResourceManagerError("Resource id must not be empty")
    if ":" not in value:
        return f"tool:{value}"
    if not value.startswith("tool:"):
        raise ResourceManagerError("ECC-FE CLI currently installs tool resources only")
    return value


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _publish(
    progress: ProgressCallback | None, name: str, phase: str, value: float
) -> None:
    if progress:
        progress(
            {
                "kind": "resource_progress",
                "resource_id": f"tool:{name}",
                "phase": phase,
                "progress": value,
            }
        )


def _verify_sha256(path: Path, expected: str) -> None:
    if not _valid_sha256(expected.lower()):
        raise ResourceManagerError("Invalid SHA256 checksum in resource registry")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected.lower():
        raise ResourceManagerError(
            f"SHA256 verification failed for {path.name}: expected {expected}, received {actual}"
        )


def _extract_archive(archive: Path, destination: Path) -> None:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                relative = _safe_relative_path(member.filename)
                target = destination.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ResourceManagerError(
                        "Symbolic links are not allowed in zip resources"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if mode:
                    target.chmod(mode & 0o777)
        return
    try:
        with tarfile.open(archive, mode="r:*") as bundle:
            members = bundle.getmembers()
            for member in members:
                relative = _safe_relative_path(member.name)
                if not (
                    member.isfile()
                    or member.isdir()
                    or member.issym()
                    or member.islnk()
                ):
                    raise ResourceManagerError(
                        f"Unsupported special file in resource archive: {member.name}"
                    )
                if member.issym() or member.islnk():
                    link_parent = PurePosixPath(*relative.parts).parent
                    link_target = link_parent / PurePosixPath(member.linkname)
                    _safe_relative_path(str(link_target))
            bundle.extractall(destination, members=members)
    except (tarfile.TarError, OSError) as error:
        raise ResourceManagerError(
            f"Unable to extract resource archive: {error}"
        ) from error


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ResourceManagerError(f"Archive path escapes destination: {value}")
    return path


def _payload_root(stage: Path, strip_prefix: str | None) -> Path:
    if strip_prefix:
        relative = _safe_relative_path(strip_prefix)
        payload = stage.joinpath(*relative.parts)
        if not payload.is_dir():
            raise ResourceManagerError(
                f"Archive strip_prefix was not found: {strip_prefix}"
            )
        return payload
    children = list(stage.iterdir())
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return stage


def _detect_executables(root: Path) -> list[str]:
    candidates: list[Path] = []
    for directory in (root / "bin", root):
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.iterdir()):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                candidates.append(candidate.resolve())
    return [str(item) for item in candidates]


def _preferred_executable(name: str, executables: list[str]) -> str:
    aliases = {
        "ecc-fe": ("ecc-fe", "fecompiler"),
        "riscv-toolchain": ("riscv64-unknown-elf-gcc",),
    }.get(name, (name,))
    for alias in aliases:
        for executable in executables:
            if Path(executable).name == alias:
                return executable
    return executables[0] if executables else ""


def _install_record(
    name: str, version: str, status: str, path: object
) -> dict[str, object]:
    return {
        "kind": "resource_install",
        "resource_id": f"tool:{name}",
        "status": status,
        "version": version,
        "path": str(path) if path else None,
    }


def _dedupe_install_records(
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_resource: dict[object, dict[str, object]] = {}
    for record in records:
        by_resource[record.get("resource_id")] = record
    return list(by_resource.values())


@contextmanager
def _manifest_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
