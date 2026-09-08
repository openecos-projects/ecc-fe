from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from fecompiler.cli.resource_manager import ResourceManager, ResourceManagerError
from fecompiler.resources import current_registry_platform


def _archive(
    tmp_path: Path, *, traversal: bool = False, executable: bool = True
) -> tuple[Path, str, int]:
    archive = tmp_path / ("bad.tar.gz" if traversal else "slang.tar.gz")
    if traversal:
        with tarfile.open(archive, "w:gz") as bundle:
            content = b"escaped"
            member = tarfile.TarInfo("../escaped")
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    else:
        root = tmp_path / "payload" / "slang-1" / "bin"
        root.mkdir(parents=True)
        if executable:
            slang = root / "slang"
            slang.write_text("#!/bin/sh\necho slang 1\n", encoding="utf-8")
            slang.chmod(0o755)
        else:
            (root.parent / "README").write_text("no executable", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(root.parent, arcname="slang-1")
    content = archive.read_bytes()
    return archive, hashlib.sha256(content).hexdigest(), len(content)


def _registry(path: Path, archive: Path, sha256: str, size: int) -> Path:
    payload = {
        "schema_version": 2,
        "tools": [
            {
                "name": "ecc-fe",
                "display_name": "ECC-FE",
                "versions": [
                    {
                        "version": "test",
                        "requires": ["tool:slang"],
                        "platforms": {
                            current_registry_platform(): {
                                "url": archive.as_uri(),
                                "sha256": sha256,
                                "size": size,
                                "strip_prefix": "slang-1",
                            }
                        },
                    }
                ],
            },
            {
                "name": "slang",
                "display_name": "Slang",
                "versions": [
                    {
                        "version": "1",
                        "requires": [],
                        "platforms": {
                            current_registry_platform(): {
                                "url": archive.as_uri(),
                                "sha256": sha256,
                                "size": size,
                                "strip_prefix": "slang-1",
                            }
                        },
                    }
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _manager(tmp_path: Path, registry: Path) -> ResourceManager:
    return ResourceManager(
        registry_url=registry.as_uri(),
        manifest_path=tmp_path / "state" / "manifest.json",
        tools_dir=tmp_path / "tools",
        cache_dir=tmp_path / "cache",
    )


def test_install_verifies_and_writes_desktop_compatible_manifest(tmp_path) -> None:
    archive, sha256, size = _archive(tmp_path)
    manager = _manager(
        tmp_path, _registry(tmp_path / "registry.json", archive, sha256, size)
    )

    records = manager.install("slang")

    assert records[0]["status"] == "installed"
    destination = tmp_path / "tools" / "slang" / "1"
    assert (destination / "bin" / "slang").is_file()
    manifest = json.loads((tmp_path / "state" / "manifest.json").read_text())
    entry = manifest["installed"]["tool:slang"]
    assert manifest["schema_version"] == 3
    assert entry["type"] == "tool"
    assert entry["sha256"] == sha256
    assert entry["active"] is True
    assert entry["managed"] is True
    assert entry["executable"].endswith("/bin/slang")


def test_install_is_idempotent_when_version_checksum_and_health_match(tmp_path) -> None:
    archive, sha256, size = _archive(tmp_path)
    manager = _manager(
        tmp_path, _registry(tmp_path / "registry.json", archive, sha256, size)
    )
    manager.install("slang")

    records = manager.install("slang")

    assert records[0]["status"] == "already_installed"


def test_install_required_resolves_ecc_fe_dependency_set(tmp_path) -> None:
    archive, sha256, size = _archive(tmp_path)
    manager = _manager(
        tmp_path, _registry(tmp_path / "registry.json", archive, sha256, size)
    )

    records = manager.install_required()

    assert [record["resource_id"] for record in records] == ["tool:slang"]
    manifest = json.loads((tmp_path / "state" / "manifest.json").read_text())
    assert manifest["required_resources"] == ["tool:slang"]


def test_checksum_failure_does_not_publish_destination_or_manifest(tmp_path) -> None:
    archive, _, size = _archive(tmp_path)
    registry = _registry(tmp_path / "registry.json", archive, "f" * 64, size)
    manager = _manager(tmp_path, registry)

    with pytest.raises(ResourceManagerError, match="SHA256 verification failed"):
        manager.install("slang")

    assert not (tmp_path / "tools" / "slang" / "1").exists()
    assert not (tmp_path / "state" / "manifest.json").exists()


def test_archive_path_traversal_is_rejected(tmp_path) -> None:
    archive, sha256, size = _archive(tmp_path, traversal=True)
    registry = _registry(tmp_path / "registry.json", archive, sha256, size)
    manager = _manager(tmp_path, registry)

    with pytest.raises(ResourceManagerError, match="escapes destination"):
        manager.install("slang")

    assert not (tmp_path / "escaped").exists()


def test_runtime_tool_without_executable_is_rejected(tmp_path) -> None:
    archive, sha256, size = _archive(tmp_path, executable=False)
    registry = _registry(tmp_path / "registry.json", archive, sha256, size)
    manager = _manager(tmp_path, registry)

    with pytest.raises(ResourceManagerError, match="does not contain an executable"):
        manager.install("slang")

    assert not (tmp_path / "tools" / "slang" / "1").exists()


def test_list_reports_unsupported_platform_per_resource(tmp_path, monkeypatch) -> None:
    archive, sha256, size = _archive(tmp_path)
    registry_path = _registry(tmp_path / "registry.json", archive, sha256, size)
    registry = json.loads(registry_path.read_text())
    for tool in registry["tools"]:
        tool["versions"][0]["platforms"] = {"not-this-platform": {}}
    manager = _manager(tmp_path, registry_path)
    monkeypatch.setattr(manager, "registry", lambda: registry)

    records = manager.list_resources()

    assert [record["status"] for record in records] == ["unavailable", "unavailable"]
    assert all("unavailable on" in record["availability_error"] for record in records)


def test_list_verifies_release_metadata_against_registry_lock(tmp_path) -> None:
    archive, sha256, size = _archive(tmp_path)
    registry_path = _registry(tmp_path / "registry.json", archive, sha256, size)
    manager = _manager(tmp_path, registry_path)
    manager.install("slang")
    registry = json.loads(registry_path.read_text())
    metadata = tmp_path / "slang.metadata.json"
    metadata.write_text(json.dumps({"sha256": sha256, "size": size}))
    slang_asset = registry["tools"][1]["versions"][0]["platforms"][
        current_registry_platform()
    ]
    slang_asset["metadata_url"] = metadata.as_uri()

    records = manager.list_resources(registry=registry)

    slang = next(record for record in records if record["resource_id"] == "tool:slang")
    assert slang["status"] == "installed"


def test_list_rejects_release_metadata_that_differs_from_registry_lock(
    tmp_path,
) -> None:
    archive, sha256, size = _archive(tmp_path)
    registry_path = _registry(tmp_path / "registry.json", archive, sha256, size)
    manager = _manager(tmp_path, registry_path)
    registry = json.loads(registry_path.read_text())
    metadata = tmp_path / "slang.metadata.json"
    metadata.write_text(json.dumps({"sha256": "f" * 64, "size": size + 1}))
    registry["tools"][1]["versions"][0]["platforms"][current_registry_platform()][
        "metadata_url"
    ] = metadata.as_uri()

    records = manager.list_resources(registry=registry)

    slang = next(record for record in records if record["resource_id"] == "tool:slang")
    assert slang["status"] == "unavailable"
    assert "does not match the registry" in slang["availability_error"]


def test_registry_asset_requires_an_approved_size_lock(tmp_path) -> None:
    archive, sha256, size = _archive(tmp_path)
    registry_path = _registry(tmp_path / "registry.json", archive, sha256, size)
    manager = _manager(tmp_path, registry_path)
    registry = json.loads(registry_path.read_text())
    registry["tools"][1]["versions"][0]["platforms"][current_registry_platform()].pop(
        "size"
    )

    with pytest.raises(ResourceManagerError, match="size lock"):
        manager.resolve_version(registry, "slang")
