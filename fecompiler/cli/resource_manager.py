from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tarfile
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fecompiler.cli import resource_cleanup, resource_download
from fecompiler.cli.resource_cleanup import CleanupError
from fecompiler.cli.resource_download import DownloadError
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
from fecompiler.utility.atomic_write import atomic_write_json

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

# The registry as it was last read, for when the network cannot be reached.
REGISTRY_CACHE_NAME = "registry.json"
LIST_METADATA_TIMEOUT = 10.0


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
        # Set when the registry could not be read and the saved copy was used.
        self.registry_note: str | None = None

    def registry(self) -> dict[str, Any]:
        """Read the registry; without a network, the copy saved the last time.

        ``ECOS_REGISTRY_URL`` may also name a local file. Everything taken
        from a registry is checked against the size and SHA256 it gives, so a
        saved copy is as safe as a fresh one; it may only be older.
        """
        location = self.registry_url
        saved = self.cache_dir / REGISTRY_CACHE_NAME
        local = Path(location).expanduser() if "://" not in location else None
        try:
            if local is not None:
                value = json.loads(local.read_text(encoding="utf-8"))
            else:
                with urllib.request.urlopen(location, timeout=30) as response:
                    value = json.load(response)
        except (OSError, ValueError) as error:
            if local is not None or not saved.is_file():
                raise ResourceManagerError(
                    f"Unable to read resource registry {location}: {error}"
                ) from error
            try:
                value = json.loads(saved.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ResourceManagerError(
                    f"Unable to read resource registry {location}: {error}"
                ) from error
            self.registry_note = (
                f"the resource registry could not be read ({error}); "
                f"using the copy saved at {saved}"
            )
        else:
            self.registry_note = None
        _check_registry(value)
        if local is None and self.registry_note is None:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(saved, value)
            except OSError:
                pass
        return value

    def list_resources(
        self, *, registry: dict[str, Any] | None = None
    ) -> list[dict[str, object]]:
        source = registry or self.registry()
        manifest = read_resource_manifest(self.manifest_path, strict=True)
        installed = installed_tool_entries(manifest)
        records: list[dict[str, object]] = []
        tools = [tool for tool in source.get("tools", []) if isinstance(tool, dict)]

        def resolve(tool: dict[str, Any]) -> tuple[RegistryVersion | None, str | None]:
            name = str(tool.get("name", ""))
            try:
                latest = self.resolve_version(source, name) if name else None
                if latest and latest.asset.metadata_url:
                    # A listing waits less for one answer than an install does.
                    self._resolve_metadata(latest.asset, timeout=LIST_METADATA_TIMEOUT)
            except ResourceManagerError as error:
                return None, str(error)
            return latest, None

        # Each tool may need a request for its release metadata; one slow
        # answer must not hold up the others.
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="ecc-fe-registry") as pool:
            resolved = list(pool.map(resolve, tools))
        for tool, (latest, availability_error) in zip(tools, resolved, strict=True):
            name = str(tool.get("name", ""))
            entry = installed.get(name)
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

    def list_installed(self) -> list[dict[str, object]]:
        """List what is installed from the manifest alone, without a network.

        Whether a newer version exists is not known here; ``list_resources``
        asks the registry for that.
        """
        manifest = read_resource_manifest(self.manifest_path, strict=True)
        records: list[dict[str, object]] = []
        for name, entry in installed_tool_entries(manifest).items():
            health = tool_entry_health(entry)
            records.append(
                {
                    "kind": "resource",
                    "resource_id": f"tool:{name}",
                    "name": name,
                    "display_name": name,
                    "status": "installed" if health.get("status") == "ok" else str(health.get("status")),
                    "installed_version": entry.get("version"),
                    "available_version": None,
                    "platform": current_registry_platform(),
                    "path": entry.get("path"),
                    "requires": [],
                    "missing_markers": health.get("missing_markers", []),
                    "availability_error": None,
                }
            )
        return records

    def uninstall(self, resource_id: str) -> dict[str, object]:
        """Remove one installed resource; see ``resource_cleanup.uninstall``."""
        name = _normalize_resource_id(resource_id).removeprefix("tool:")
        try:
            return resource_cleanup.uninstall(
                name,
                manifest_path=self.manifest_path,
                tools_dir=self.tools_dir,
                lock=_manifest_lock,
            )
        except CleanupError as error:
            raise ResourceManagerError(str(error)) from error

    def prune(self, *, apply: bool) -> list[dict[str, object]]:
        """Report, and with *apply* remove, old versions and leftovers."""
        with _manifest_lock(self.manifest_path):
            return resource_cleanup.prune(
                manifest_path=self.manifest_path,
                tools_dir=self.tools_dir,
                cache_dir=self.cache_dir,
                apply=apply,
            )

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
        archive: Path | None = None,
        _visiting: set[str] | None = None,
    ) -> list[dict[str, object]]:
        """Install a resource and what it requires.

        *archive* is a copy of the resource's archive that is already on this
        machine; it is used for the named resource instead of downloading,
        after the same size and checksum test.
        """
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
            records.append(
                self._install_one(name, selected, progress=progress, archive=archive)
            )
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
        archive: Path | None = None,
    ) -> dict[str, object]:
        asset = self._resolve_metadata(selected.asset, unreachable_ok=True)
        manifest = read_resource_manifest(self.manifest_path, strict=True)
        existing = installed_tool_entries(manifest).get(name)
        # An install of this tool that was killed may have left the installed
        # directory moved aside; put that right before judging its health.
        resource_cleanup.recover_staging(
            self.tools_dir / name,
            installed_path=(
                Path(str(existing.get("path"))) if existing and existing.get("path") else None
            ),
        )
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
        # Named by content, so an interrupted download is found again and a
        # complete one is not fetched twice.
        archive_path = self.cache_dir / f"{name}-{selected.version}-{asset.sha256[:16]}.archive"
        installed = False
        extract_parent = self.tools_dir / name
        extract_parent.mkdir(parents=True, exist_ok=True)
        stage_path = extract_parent / f".extract-{selected.version}-{uuid.uuid4().hex}"
        destination = extract_parent / selected.version
        backup_path = extract_parent / f".backup-{selected.version}-{uuid.uuid4().hex}"
        try:
            if archive is not None:
                _publish(progress, name, "verifying", 0.0)
                if not archive.is_file():
                    raise ResourceManagerError(f"Archive not found: {archive}")
                if not resource_download.is_complete(
                    archive, size=asset.size, sha256=asset.sha256
                ):
                    raise ResourceManagerError(
                        f"{archive} is not the archive of {name} {selected.version}: "
                        f"the registry names {asset.size} bytes with SHA256 {asset.sha256}"
                    )
                source_archive = archive
            else:
                _publish(progress, name, "downloading", 0.0)
                self._download(
                    asset.url,
                    archive_path,
                    expected_size=asset.size,
                    sha256=asset.sha256,
                    progress=progress,
                    resource_name=name,
                )
                source_archive = archive_path
            stage_path.mkdir(parents=True)
            _extract_archive(source_archive, stage_path)
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
            installed = True
            return _install_record(name, selected.version, "installed", destination)
        finally:
            # A downloaded archive is kept until the install has succeeded, so
            # a failure after the download does not cost the download again.
            if installed:
                archive_path.unlink(missing_ok=True)
                archive_path.with_name(archive_path.name + ".part").unlink(missing_ok=True)
            if stage_path.exists():
                shutil.rmtree(stage_path)
            if backup_path.exists():
                if destination.exists():
                    shutil.rmtree(backup_path)
                else:
                    os.replace(backup_path, destination)

    def _resolve_metadata(
        self,
        asset: RegistryAsset,
        *,
        unreachable_ok: bool = False,
        timeout: float = 30,
    ) -> RegistryAsset:
        """Check the registry's lock against the release's own metadata.

        With *unreachable_ok*, metadata that cannot be read is skipped: the
        archive is still held to the registry's size and SHA256, and an
        install has to work without a network.
        """
        if not asset.metadata_url:
            return asset
        try:
            with urllib.request.urlopen(asset.metadata_url, timeout=timeout) as response:
                metadata = json.load(response)
        except OSError as error:
            if unreachable_ok:
                return asset
            raise ResourceManagerError(
                f"Unable to read release metadata: {error}"
            ) from error
        except ValueError as error:
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
        sha256: str,
        progress: ProgressCallback | None = None,
        resource_name: str = "",
    ) -> None:
        """Fetch *url* to *destination*, checked against its size and SHA256."""

        def notify(phase: str, fields: dict[str, object]) -> None:
            if not progress or not resource_name:
                return
            total = fields.get("total")
            received = fields.get("received")
            done = (
                float(received) / float(total)
                if isinstance(received, int) and isinstance(total, int) and total
                else 0.0
            )
            event: dict[str, object] = {
                "kind": "resource_progress",
                "resource_id": f"tool:{resource_name}",
                "phase": phase,
                "progress": round(min(1.0, done), 2),
            }
            event.update({key: value for key, value in fields.items() if key not in event})
            progress(event)

        try:
            resource_download.fetch(
                url, destination, size=expected_size, sha256=sha256, notify=notify
            )
        except DownloadError as error:
            raise ResourceManagerError(str(error)) from error

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
            self._download(url, destination, expected_size=size, sha256=sha256)

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
            atomic_write_json(self.manifest_path, manifest)

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
            atomic_write_json(self.manifest_path, manifest)


def _check_registry(value: object) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise ResourceManagerError("Unsupported or invalid resource registry schema")
    if not isinstance(value.get("tools"), list):
        raise ResourceManagerError("Resource registry tools field must be an array")


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
