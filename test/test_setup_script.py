from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from fecompiler.resources import current_registry_platform


def _fake_runtime_registry(
    tmp_path: Path,
    *,
    version: str = "test",
    requires: tuple[str, ...] = (),
) -> Path:
    runtime = tmp_path / "payload" / f"ecc-fe-{version}"
    binary = runtime / "bin" / "ecc-fe"
    binary.parent.mkdir(parents=True)
    (runtime / "fecompiler").mkdir()
    binary.write_text(
        """#!/bin/sh
case "${1:-}" in
  --version) echo "ecc-fe test" ;;
  resource)
    mkdir -p "$XDG_DATA_HOME/ecos-studio/tools/partial/1"
    printf 'partial resource\n' > "$XDG_DATA_HOME/ecos-studio/tools/partial/1/data"
    if [ "${SETUP_TEST_RESOURCE_FAIL:-0}" = "1" ]; then exit 17; fi
    printf '%s\n' "$XDG_STATE_HOME" > "${SETUP_TEST_MARKER:-/dev/null}"
    if [ -n "${SETUP_TEST_CONCURRENT_MANIFEST:-}" ]; then
      mkdir -p "$(dirname "$SETUP_TEST_CONCURRENT_MANIFEST")"
      if [ -n "${SETUP_TEST_CONCURRENT_MANIFEST_CONTENT:-}" ]; then
        printf '%s\n' "$SETUP_TEST_CONCURRENT_MANIFEST_CONTENT" > "$SETUP_TEST_CONCURRENT_MANIFEST"
      else
        printf '%s\n' '{"schema_version":3,"resources_dir":"concurrent-resources","tools_dir":"concurrent-tools","installed":{"tool:concurrent":{"type":"tool","name":"concurrent","version":"1","path":"concurrent-tools/concurrent/1"}},"required_resources":["tool:concurrent"]}' > "$SETUP_TEST_CONCURRENT_MANIFEST"
      fi
    fi
    ;;
  doctor) echo "doctor ready" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    archive = tmp_path / f"ecc-fe-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(runtime, arcname="ecc-fe-test")
    content = archive.read_bytes()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tools": [
                    {
                        "name": "ecc-fe",
                        "versions": [
                            {
                                "version": version,
                                "requires": list(requires),
                                "platforms": {
                                    current_registry_platform(): {
                                        "url": archive.as_uri(),
                                        "sha256": hashlib.sha256(content).hexdigest(),
                                        "size": len(content),
                                        "strip_prefix": "ecc-fe-test",
                                    }
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry


def test_setup_script_installs_runtime_manifest_links_and_required_resources(
    tmp_path,
) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    marker = tmp_path / "resource-install-marker"
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
        "SETUP_TEST_MARKER": str(marker),
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(
        (tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json").read_text()
    )
    entry = manifest["installed"]["tool:ecc-fe"]
    assert entry["version"] == "test"
    assert entry["active"] is True
    assert manifest["required_resources"] == []
    assert manifest["pdks_dir"] == str(tmp_path / "data" / "ecos-studio" / "pdks")
    assert manifest["mpcs_dir"] == str(tmp_path / "data" / "ecos-studio" / "mpcs")
    assert (tmp_path / "bin" / "ecc-fe").is_symlink()
    assert (tmp_path / "bin" / "fecompiler").is_symlink()
    assert all(Path(path).is_file() for path in entry["detected_executables"])
    assert not (tmp_path / "data" / "ecos-studio" / "tools" / "partial").exists()
    marker_state_home = marker.read_text().strip()
    assert marker_state_home != ""
    assert marker_state_home != str(tmp_path / "state")
    assert marker_state_home.endswith("/state")
    assert "ecc-fe test" in result.stdout


def test_setup_script_rejects_conflicting_modes(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            str(repository / "docs" / "ecc-fe-cli-setup.sh"),
            "--runtime-only",
            "--check-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "cannot be combined" in result.stderr


def test_setup_script_does_not_activate_a_runtime_when_required_resources_fail(
    tmp_path,
) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    marker = tmp_path / "resource-install-marker"
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
        "SETUP_TEST_MARKER": str(marker),
        "SETUP_TEST_RESOURCE_FAIL": "1",
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    manifest_path = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["required_resources"] == []
    assert not (tmp_path / "bin" / "ecc-fe").exists()
    assert not (tmp_path / "bin" / "fecompiler").exists()
    assert not (tmp_path / "data" / "ecos-studio" / "tools" / "ecc-fe" / "test").exists()
    assert not (tmp_path / "data" / "ecos-studio" / "tools" / "partial").exists()


def test_setup_script_records_required_resources_before_dependency_failure(tmp_path) -> None:
    registry = _fake_runtime_registry(
        tmp_path, requires=("tool:missing", "tool:another-missing")
    )
    repository = Path(__file__).resolve().parents[1]
    manifest_path = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
        "SETUP_TEST_RESOURCE_FAIL": "1",
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["required_resources"] == [
        "tool:missing",
        "tool:another-missing",
    ]
    assert "tool:ecc-fe" not in manifest["installed"]
    assert not (tmp_path / "bin" / "ecc-fe").exists()
    assert not (tmp_path / "data" / "ecos-studio" / "tools" / "ecc-fe" / "test").exists()


@pytest.mark.parametrize("raw_manifest", ("null\n", "[]\n"))
def test_setup_script_preserves_falsey_malformed_manifest(tmp_path, raw_manifest) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    manifest_path = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(raw_manifest, encoding="utf-8")
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert manifest_path.read_text(encoding="utf-8") == raw_manifest


def test_setup_script_merges_manifest_changes_created_during_resource_install(tmp_path) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    concurrent_manifest = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
        "SETUP_TEST_CONCURRENT_MANIFEST": str(concurrent_manifest),
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(concurrent_manifest.read_text(encoding="utf-8"))
    assert "tool:ecc-fe" in manifest["installed"]
    assert "tool:concurrent" in manifest["installed"]
    assert "tool:concurrent" in manifest["required_resources"]


@pytest.mark.parametrize("raw_manifest", ("null", "[]"))
def test_setup_script_rejects_falsey_manifest_replaced_during_resource_install(
    tmp_path, raw_manifest
) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    concurrent_manifest = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
        "SETUP_TEST_CONCURRENT_MANIFEST": str(concurrent_manifest),
        "SETUP_TEST_CONCURRENT_MANIFEST_CONTENT": raw_manifest,
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert concurrent_manifest.read_text(encoding="utf-8") == f"{raw_manifest}\n"


def test_setup_script_drops_requirements_removed_by_runtime_upgrade(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[1]
    old_registry = _fake_runtime_registry(
        tmp_path / "old", version="old", requires=("tool:obsolete",)
    )
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": old_registry.as_uri(),
    }
    old = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert old.returncode == 0, old.stderr

    new_registry = _fake_runtime_registry(tmp_path / "new", version="new")
    env["ECOS_REGISTRY_URL"] = new_registry.as_uri()
    new = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert new.returncode == 0, new.stderr

    manifest_path = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["required_resources"] == []


def test_setup_script_preserves_concurrent_manifest_when_publish_rolls_back(tmp_path) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    concurrent_manifest = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    bin_file = tmp_path / "bin-file"
    bin_file.write_text("occupied", encoding="utf-8")
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(bin_file),
        "ECOS_REGISTRY_URL": registry.as_uri(),
        "SETUP_TEST_CONCURRENT_MANIFEST": str(concurrent_manifest),
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    manifest = json.loads(concurrent_manifest.read_text(encoding="utf-8"))
    assert "tool:concurrent" in manifest["installed"]


def test_setup_script_preserves_manifest_directory_collision(tmp_path) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    manifest_directory = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    manifest_directory.mkdir(parents=True)
    sentinel = manifest_directory / "keep-me"
    sentinel.write_text("preserve", encoding="utf-8")
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh"), "--runtime-only"],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert manifest_directory.is_dir()


@pytest.mark.parametrize("name", ("ecc-fe", "fecompiler"))
def test_setup_script_rejects_directory_at_cli_link_path(tmp_path, name) -> None:
    registry = _fake_runtime_registry(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    link_directory = tmp_path / "bin" / name
    link_directory.mkdir(parents=True)
    sentinel = link_directory / "keep-me"
    sentinel.write_text("preserve", encoding="utf-8")
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry.as_uri(),
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh"), "--runtime-only"],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert link_directory.is_dir()


def test_setup_script_preserves_active_runtime_when_upgrade_resources_fail(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[1]
    first_registry = _fake_runtime_registry(tmp_path / "first", version="old")
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": first_registry.as_uri(),
    }
    first = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr

    manifest_path = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    old_manifest = manifest_path.read_bytes()
    old_link = (tmp_path / "bin" / "ecc-fe").resolve()
    old_runtime = tmp_path / "data" / "ecos-studio" / "tools" / "ecc-fe" / "old"
    assert old_link == old_runtime / "bin" / "ecc-fe"

    second_registry = _fake_runtime_registry(tmp_path / "second", version="new")
    env["ECOS_REGISTRY_URL"] = second_registry.as_uri()
    env["SETUP_TEST_RESOURCE_FAIL"] = "1"
    second = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh")],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert second.returncode != 0
    assert manifest_path.read_bytes() == old_manifest
    assert (tmp_path / "bin" / "ecc-fe").resolve() == old_link
    assert old_runtime.is_dir()
    assert not (tmp_path / "data" / "ecos-studio" / "tools" / "ecc-fe" / "new").exists()


def test_setup_script_rejects_metadata_that_differs_from_registry_lock(
    tmp_path,
) -> None:
    registry_path = _fake_runtime_registry(tmp_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    metadata = tmp_path / "ecc-fe.metadata.json"
    metadata.write_text(json.dumps({"sha256": "f" * 64, "size": 1}), encoding="utf-8")
    asset = registry["tools"][0]["versions"][0]["platforms"][
        current_registry_platform()
    ]
    asset["metadata_url"] = metadata.as_uri()
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    repository = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "ECC_FE_BIN_DIR": str(tmp_path / "bin"),
        "ECOS_REGISTRY_URL": registry_path.as_uri(),
    }

    result = subprocess.run(
        [str(repository / "docs" / "ecc-fe-cli-setup.sh"), "--runtime-only"],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "does not match the registry" in result.stderr
