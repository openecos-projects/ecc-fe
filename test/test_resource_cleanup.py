"""Listing without a network, uninstalling, pruning, and repairing a killed install."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import time
from pathlib import Path

import pytest

from fecompiler.cli import main as cli_main
from fecompiler.cli import resource_cleanup
from fecompiler.cli.resource_manager import ResourceManager, ResourceManagerError
from fecompiler.resources import current_registry_platform

OLD = time.time() - 2 * resource_cleanup.RECENT_SECONDS


def _archive(tmp_path: Path, version: str) -> tuple[Path, str, int]:
    root = tmp_path / f"payload-{version}" / "slang" / "bin"
    root.mkdir(parents=True)
    slang = root / "slang"
    slang.write_text(f"#!/bin/sh\necho slang {version}\n", encoding="utf-8")
    slang.chmod(0o755)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        bundle.add(root.parent, arcname="slang")
    archive = tmp_path / f"slang-{version}.tar.gz"
    archive.write_bytes(buffer.getvalue())
    return archive, hashlib.sha256(buffer.getvalue()).hexdigest(), len(buffer.getvalue())


def _registry(tmp_path: Path, *versions: str) -> Path:
    """A registry whose newest slang version is the first one given."""
    entries = []
    for version in versions:
        archive, sha256, size = _archive(tmp_path, version)
        entries.append(
            {
                "version": version,
                "requires": [],
                "platforms": {
                    current_registry_platform(): {
                        "url": archive.as_uri(),
                        "sha256": sha256,
                        "size": size,
                        "strip_prefix": "slang",
                    }
                },
            }
        )
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tools": [
                    {"name": "ecc-fe", "versions": [{**entries[0], "requires": ["tool:slang"]}]},
                    {"name": "slang", "display_name": "Slang", "versions": entries},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _manager(tmp_path: Path, registry: Path) -> ResourceManager:
    return ResourceManager(
        registry_url=str(registry),
        manifest_path=tmp_path / "state" / "manifest.json",
        tools_dir=tmp_path / "tools",
        cache_dir=tmp_path / "cache",
    )


def _age(path: Path, when: float = OLD) -> None:
    os.utime(path, (when, when))


@pytest.fixture
def manager(tmp_path) -> ResourceManager:
    instance = _manager(tmp_path, _registry(tmp_path, "2", "1"))
    instance.install("slang", version="1")
    instance.install("slang")
    return instance


# --- list --installed ---------------------------------------------------------


def test_installed_resources_are_listed_without_the_registry(manager, tmp_path) -> None:
    manager.registry_url = "http://127.0.0.1:9/unreachable.json"

    (record,) = manager.list_installed()

    assert (record["resource_id"], record["status"], record["installed_version"]) == (
        "tool:slang",
        "installed",
        "2",
    )
    assert record["available_version"] is None
    assert record["path"] == str((tmp_path / "tools" / "slang" / "2").resolve())


def test_a_damaged_install_is_listed_as_such(manager, tmp_path) -> None:
    (tmp_path / "tools" / "slang" / "2" / "bin" / "slang").unlink()

    (record,) = manager.list_installed()

    assert record["status"] == "invalid"
    assert record["missing_markers"] == ["bin/slang"]


# --- prune --------------------------------------------------------------------


def test_installing_a_newer_version_leaves_the_older_one_for_prune(manager, tmp_path) -> None:
    old = tmp_path / "tools" / "slang" / "1"
    assert old.is_dir()

    (record,) = manager.prune(apply=False)

    assert record == {
        "kind": "resource_prune",
        "status": "would_remove",
        "category": "old_version",
        "path": str(old),
        "bytes": resource_cleanup.directory_bytes(old),
        "recent": True,
        "resource_id": "tool:slang",
        "version": "1",
        "installed_version": "2",
    }
    assert old.is_dir()


def test_prune_apply_removes_old_versions_and_keeps_the_installed_one(manager, tmp_path) -> None:
    (record,) = manager.prune(apply=True)

    assert record["status"] == "removed"
    assert not (tmp_path / "tools" / "slang" / "1").exists()
    assert (tmp_path / "tools" / "slang" / "2" / "bin" / "slang").is_file()
    assert manager.prune(apply=True) == []
    assert manager.list_installed()[0]["status"] == "installed"


def test_leftovers_of_a_killed_install_and_unfinished_downloads_are_pruned(manager, tmp_path) -> None:
    tool = tmp_path / "tools" / "slang"
    stale = [tool / ".extract-3-abc", tool / ".backup-1-def"]
    for path in stale:
        (path / "sub").mkdir(parents=True)
        (path / "sub" / "file").write_bytes(b"x" * 10)
        _age(path)
    cache = tmp_path / "cache"
    downloads = [cache / "yosys-1-0123.archive.part", cache / "yosys-1-0123.archive", cache / "old-uuid.download"]
    for path in downloads:
        path.write_bytes(b"y" * 7)
        _age(path)
    (cache / "registry.json").write_text("{}", encoding="utf-8")

    records = manager.prune(apply=True)

    by_category = {}
    for record in records:
        by_category.setdefault(record["category"], []).append(record)
    assert [r["status"] for r in records] == ["removed"] * 6
    assert len(by_category["staging"]) == 2 and len(by_category["download"]) == 3
    assert not any(path.exists() for path in (*stale, *downloads))
    # The saved registry is not a download.
    assert (cache / "registry.json").is_file()


def test_what_may_belong_to_a_running_install_is_kept(manager, tmp_path) -> None:
    staging = tmp_path / "tools" / "slang" / ".extract-3-live"
    staging.mkdir()
    partial = tmp_path / "cache" / "yosys-1-0123.archive.part"
    partial.write_bytes(b"y")

    records = [record for record in manager.prune(apply=True) if record["category"] != "old_version"]

    assert [(record["category"], record["status"]) for record in records] == [
        ("staging", "kept_recent"),
        ("download", "kept_recent"),
    ]
    assert staging.is_dir() and partial.is_file()


def test_versions_of_a_tool_that_is_not_installed_are_left_alone(manager, tmp_path) -> None:
    other = tmp_path / "tools" / "verilator" / "5.0"
    other.mkdir(parents=True)

    records = manager.prune(apply=True)

    assert [record["resource_id"] for record in records] == ["tool:slang"]
    assert other.is_dir()


def test_versions_are_not_judged_when_the_installed_one_is_elsewhere(manager, tmp_path) -> None:
    elsewhere = tmp_path / "opt" / "slang"
    (elsewhere / "bin").mkdir(parents=True)
    path = tmp_path / "state" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["installed"]["tool:slang"]["path"] = str(elsewhere)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert manager.prune(apply=True) == []
    assert (tmp_path / "tools" / "slang" / "1").is_dir()


def test_prune_of_an_empty_installation_finds_nothing(tmp_path) -> None:
    assert _manager(tmp_path, _registry(tmp_path, "1")).prune(apply=True) == []


# --- uninstall ----------------------------------------------------------------


def test_uninstall_removes_the_entry_and_the_files(manager, tmp_path) -> None:
    record = manager.uninstall("slang")

    assert (record["status"], record["files"], record["version"]) == ("uninstalled", "removed", "2")
    assert record["bytes"] > 0 and record["required"] is False
    assert not (tmp_path / "tools" / "slang" / "2").exists()
    assert manager.list_installed() == []
    # The older version was never this entry's: prune is what removes it.
    assert (tmp_path / "tools" / "slang" / "1").is_dir()


def test_uninstalling_the_last_version_removes_the_tools_own_directory(tmp_path) -> None:
    instance = _manager(tmp_path, _registry(tmp_path, "1"))
    instance.install("slang")

    instance.uninstall("tool:slang")

    assert not (tmp_path / "tools" / "slang").exists()


def test_uninstall_of_something_not_installed_is_an_error(manager) -> None:
    with pytest.raises(ResourceManagerError, match="tool:verilator is not installed"):
        manager.uninstall("verilator")


def test_files_the_resource_manager_did_not_install_are_kept(manager, tmp_path) -> None:
    elsewhere = tmp_path / "opt" / "slang"
    (elsewhere / "bin").mkdir(parents=True)
    path = tmp_path / "state" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["installed"]["tool:slang"].update(path=str(elsewhere), managed=False)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    record = manager.uninstall("slang")

    assert (record["status"], record["files"]) == ("unregistered", "kept")
    assert elsewhere.is_dir()
    assert manager.list_installed() == []


def test_a_managed_entry_pointing_outside_the_tools_directory_keeps_its_files(manager, tmp_path) -> None:
    precious = tmp_path / "home" / "work"
    precious.mkdir(parents=True)
    path = tmp_path / "state" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["installed"]["tool:slang"]["path"] = str(precious)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    record = manager.uninstall("slang")

    assert record["files"] == "kept"
    assert precious.is_dir()


def test_uninstalling_a_required_resource_is_reported(tmp_path) -> None:
    instance = _manager(tmp_path, _registry(tmp_path, "1"))
    instance.install_required()

    record = instance.uninstall("slang")

    assert record["required"] is True


# --- repair after a kill ------------------------------------------------------


def test_an_install_killed_between_the_two_renames_is_put_back(manager, tmp_path) -> None:
    tool = tmp_path / "tools" / "slang"
    # The installed directory was moved aside and the new one never arrived.
    os.replace(tool / "2", tool / ".backup-2-killed")
    assert manager.list_installed()[0]["status"] == "missing"

    (record,) = manager.install("slang")

    assert record["status"] == "already_installed"
    assert (tool / "2" / "bin" / "slang").is_file()
    assert not (tool / ".backup-2-killed").exists()


def test_stale_staging_directories_are_removed_by_the_next_install(manager, tmp_path) -> None:
    tool = tmp_path / "tools" / "slang"
    stale, live = tool / ".extract-2-old", tool / ".extract-2-live"
    stale.mkdir()
    live.mkdir()
    _age(stale)

    manager.install("slang")

    assert not stale.exists() and live.is_dir()


def test_recovery_does_nothing_for_a_tool_that_was_never_installed(tmp_path) -> None:
    assert resource_cleanup.recover_staging(tmp_path / "tools" / "none", installed_path=None) == []


def test_a_backup_of_another_version_is_not_put_in_place(manager, tmp_path) -> None:
    tool = tmp_path / "tools" / "slang"
    backup = tool / ".backup-1-other"
    backup.mkdir()
    os.replace(tool / "2", tool / "moved-away")

    actions = resource_cleanup.recover_staging(tool, installed_path=tool / "2")

    assert actions == [] and backup.is_dir() and not (tool / "2").exists()


# --- the commands -------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("ECOS_REGISTRY_URL", str(_registry(tmp_path, "2", "1")))
    monkeypatch.delenv("ECC_FE_PROGRAM", raising=False)

    def run(*args: str) -> tuple[int, list[dict], str]:
        exit_code = cli_main.run(["resource", *args, "--json"])
        captured = capsys.readouterr()
        return exit_code, json.loads(captured.out)["records"], captured.err

    assert run("install", "slang", "--version", "1")[0] == 0
    assert run("install", "slang")[0] == 0
    return run


def test_list_installed_does_not_ask_the_registry(cli, monkeypatch) -> None:
    monkeypatch.setenv("ECOS_REGISTRY_URL", "http://127.0.0.1:9/unreachable.json")

    exit_code, records, stderr = cli("list", "--installed")

    assert exit_code == 0
    assert [(r["resource_id"], r["installed_version"]) for r in records] == [("tool:slang", "2")]
    assert stderr == ""


def test_prune_reports_first_and_removes_only_with_apply(cli, tmp_path) -> None:
    old = tmp_path / "data" / "ecos-studio" / "tools" / "slang" / "1"

    exit_code, (summary, item), _ = cli("prune")

    assert exit_code == 0
    assert (summary["kind"], summary["applied"], summary["items"]) == ("resource_prune_summary", False, 1)
    assert summary["removable_bytes"] == item["bytes"] > 0 and summary["removed_bytes"] == 0
    assert summary["apply_cmd"] == "ecc-fe resource prune --apply"
    assert item["status"] == "would_remove" and old.is_dir()

    exit_code, (summary, item), _ = cli("prune", "--apply")

    assert exit_code == 0
    assert (summary["applied"], summary["removed_bytes"]) == (True, item["bytes"])
    assert "apply_cmd" not in summary
    assert item["status"] == "removed" and not old.exists()

    exit_code, (summary,), _ = cli("prune")
    assert (summary["items"], summary["removable_bytes"]) == (0, 0)


def test_prune_text_is_one_line_per_item(cli, tmp_path, capsys) -> None:
    assert cli_main.run(["resource", "prune"]) == 0

    line = capsys.readouterr().out.splitlines()[-1]
    assert line.startswith("  would_remove  old_version")
    assert line.endswith(str(tmp_path / "data" / "ecos-studio" / "tools" / "slang" / "1"))


def test_uninstall_command(cli, tmp_path) -> None:
    exit_code, (record,), _ = cli("uninstall", "slang")

    assert exit_code == 0
    assert (record["kind"], record["status"]) == ("resource_uninstall", "uninstalled")
    assert "reinstall_cmd" not in record
    assert cli("list", "--installed")[1] == []

    exit_code, (record,), _ = cli("uninstall", "slang")
    assert exit_code == 1 and record["error"] == "resource_uninstall_failed"


def test_uninstalling_a_required_resource_says_how_to_get_it_back(cli) -> None:
    assert cli("install", "--required")[0] == 0

    exit_code, (record,), _ = cli("uninstall", "slang")

    assert exit_code == 0
    assert record["required"] is True
    assert record["reinstall_cmd"] == "ecc-fe resource install slang"
    assert cli("status")[0] == 1
