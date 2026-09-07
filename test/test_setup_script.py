from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

from fecompiler.resources import current_registry_platform


def _fake_runtime_registry(tmp_path: Path) -> Path:
    runtime = tmp_path / "payload" / "ecc-fe-test"
    binary = runtime / "bin" / "ecc-fe"
    binary.parent.mkdir(parents=True)
    (runtime / "fecompiler").mkdir()
    binary.write_text(
        """#!/bin/sh
case "${1:-}" in
  --version) echo "ecc-fe test" ;;
  resource) mkdir -p "$XDG_STATE_HOME"; echo "$*" > "$XDG_STATE_HOME/resource-call" ;;
  doctor) echo "doctor ready" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    archive = tmp_path / "ecc-fe-test.tar.gz"
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
                                "version": "test",
                                "requires": [],
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

    assert result.returncode == 0, result.stderr
    manifest = json.loads(
        (tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json").read_text()
    )
    entry = manifest["installed"]["tool:ecc-fe"]
    assert entry["version"] == "test"
    assert entry["active"] is True
    assert manifest["required_resources"] == []
    assert (tmp_path / "bin" / "ecc-fe").is_symlink()
    assert (tmp_path / "bin" / "fecompiler").is_symlink()
    assert (tmp_path / "state" / "resource-call").read_text().strip() == (
        "resource install --required"
    )
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
