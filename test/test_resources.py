from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fecompiler import resources
from fecompiler.resources import (
    activate_managed_resources,
    resource_roots,
    tool_entry_health,
)


def _write_manifest(state_home: Path, entries: dict[str, dict[str, object]]) -> None:
    path = state_home / "ecos-studio" / "resources" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"schema_version": 3, "installed": entries}), encoding="utf-8"
    )


def _tool_entry(
    name: str, root: Path, executable: Path | None = None
) -> dict[str, object]:
    return {
        "type": "tool",
        "name": name,
        "version": "1",
        "path": str(root),
        "sha256": "0" * 64,
        "detected_executables": [str(executable)] if executable else [],
        "executable": str(executable or ""),
        "active": True,
        "managed": True,
    }


def test_activate_managed_resources_reads_desktop_manifest(
    tmp_path, monkeypatch
) -> None:
    state_home = tmp_path / "state"
    slang_root = tmp_path / "tools" / "slang" / "1"
    slang = slang_root / "bin" / "slang"
    slang.parent.mkdir(parents=True)
    slang.write_text("#!/bin/sh\n", encoding="utf-8")
    slang.chmod(0o755)
    example_root = tmp_path / "tools" / "ecc-fe-examples" / "1"
    example_dir = example_root / "examples" / "ysyx_00000000"
    (example_dir / "rtl").mkdir(parents=True)
    (example_dir / "filelist.cpu.f").write_text("", encoding="utf-8")
    (example_dir / "rtl" / "ysyx_00000000.sv").write_text("", encoding="utf-8")
    (example_dir / "rtl" / "ysyx_00000000_difftest.sv").write_text("", encoding="utf-8")
    _write_manifest(
        state_home,
        {
            "tool:slang": _tool_entry("slang", slang_root, slang),
            "tool:ecc-fe-examples": _tool_entry("ecc-fe-examples", example_root),
        },
    )
    env = {
        "XDG_STATE_HOME": str(state_home),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "PATH": "/usr/bin",
    }

    changed = activate_managed_resources(env)

    assert env["ECOS_SLANG"] == str(slang.resolve())
    assert env["PATH"].split(os.pathsep)[0] == str(slang.parent.resolve())
    assert env["ECOS_FE_RESOURCE_ROOTS"] == str(example_root.resolve())
    assert changed["ECOS_SLANG"] == str(slang.resolve())


def test_explicit_tool_environment_has_priority(tmp_path) -> None:
    env = {
        "XDG_STATE_HOME": str(tmp_path / "missing-state"),
        "ECOS_SLANG": "/explicit/slang",
        "PATH": "/usr/bin",
    }

    activate_managed_resources(env)

    assert env["ECOS_SLANG"] == "/explicit/slang"


def test_resource_roots_preserve_explicit_order(monkeypatch, tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv(
        "ECOS_FE_RESOURCE_ROOTS", os.pathsep.join((str(first), str(second)))
    )
    monkeypatch.delenv("ECOS_FE_SOC_ROOT", raising=False)

    assert resource_roots() == [first.resolve(), second.resolve()]


def test_frozen_runtime_discovers_sibling_fecompiler_directory(
    monkeypatch, tmp_path
) -> None:
    runtime = tmp_path / "ecc-fe" / "latest"
    binary = runtime / "bin" / "ecc-fe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    (runtime / "fecompiler").mkdir()
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "executable", str(binary))
    monkeypatch.delenv("ECOS_FE_COMPILER_ROOT", raising=False)
    env = {
        "XDG_STATE_HOME": str(tmp_path / "missing-state"),
        "PATH": "/usr/bin",
    }

    changed = activate_managed_resources(env)

    assert resources.frontend_repo_root() == runtime.resolve()
    assert env["ECOS_FE_COMPILER_ROOT"] == str(runtime.resolve())
    assert changed["ECOS_FE_COMPILER_ROOT"] == str(runtime.resolve())


@pytest.mark.parametrize("name", ["slang", "yosys"])
def test_executable_tools_require_their_named_binary(tmp_path, name: str) -> None:
    root = tmp_path / name
    root.mkdir()

    health = tool_entry_health(_tool_entry(name, root))

    assert health["status"] == "invalid"
    assert health["missing_markers"] == [f"bin/{name}"]
