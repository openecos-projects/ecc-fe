"""A tool's variables come from its managed installation as a group, or not at all."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fecompiler.resources import activate_managed_resources, required_tool_health_markers

_BINARIES = {
    "verilator": "verilator",
    "slang": "slang",
    "yosys": "yosys",
    "riscv-toolchain": "riscv64-unknown-elf-gcc",
}


def _managed(tmp_path: Path, *names: str) -> dict[str, Path]:
    """Install fake managed tools; return the root of each."""
    entries: dict[str, dict[str, object]] = {}
    roots: dict[str, Path] = {}
    for name in names:
        root = tmp_path / "tools" / name / "1"
        executable = root / "bin" / _BINARIES[name]
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        for relative, kind in required_tool_health_markers(name):
            marker = root / relative
            if kind == "directory":
                marker.mkdir(parents=True, exist_ok=True)
            elif not marker.exists():
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("#!/bin/sh\n", encoding="utf-8")
                marker.chmod(0o755)
        entries[f"tool:{name}"] = {
            "type": "tool",
            "name": name,
            "version": "1",
            "path": str(root),
            "detected_executables": [str(executable)],
            "executable": str(executable),
            "active": True,
            "managed": True,
        }
        roots[name] = root.resolve()
    manifest = tmp_path / "state" / "ecos-studio" / "resources" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"schema_version": 3, "installed": entries}), encoding="utf-8")
    return roots


def _env(tmp_path: Path, **values: str) -> dict[str, str]:
    return {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "PATH": "/usr/bin",
        **values,
    }


# --- nothing chosen: the managed tool, completely ----------------------------


def test_without_a_choice_the_managed_tools_are_used(tmp_path) -> None:
    roots = _managed(tmp_path, "verilator", "slang", "yosys", "riscv-toolchain")
    env = _env(tmp_path)

    activate_managed_resources(env)

    assert env["ECOS_VERILATOR"] == str(roots["verilator"] / "bin" / "verilator")
    assert env["VERILATOR_ROOT"] == str(roots["verilator"] / "share" / "verilator")
    assert env["ECOS_SLANG"] == str(roots["slang"] / "bin" / "slang")
    assert env["CHIPCOMPILER_OSS_CAD_DIR"] == env["ECOS_ELECTRON_OSS_CAD_DIR"] == str(roots["yosys"])
    assert env["RISCV"] == env["RISCV_TOOLCHAIN"] == str(roots["riscv-toolchain"])
    assert env["RISCV_PREFIX"] == str(roots["riscv-toolchain"] / "bin" / "riscv64-unknown-elf-")


def test_activating_twice_changes_nothing_the_second_time(tmp_path) -> None:
    _managed(tmp_path, "verilator", "slang", "yosys", "riscv-toolchain")
    env = _env(tmp_path)
    activate_managed_resources(env)
    first = dict(env)

    changed = activate_managed_resources(env)

    assert env == first
    assert changed == {}


# --- Verilator ----------------------------------------------------------------


@pytest.mark.parametrize("variable", ["ECOS_VERILATOR", "VERILATOR"])
def test_a_verilator_of_the_users_own_gets_no_managed_root(tmp_path, variable) -> None:
    _managed(tmp_path, "verilator")
    env = _env(tmp_path, **{variable: "/opt/verilator-5.2/bin/verilator"})

    activate_managed_resources(env)

    # The binary and the data directory must be of one installation.
    assert "VERILATOR_ROOT" not in env
    assert env[variable] == "/opt/verilator-5.2/bin/verilator"
    assert ("ECOS_VERILATOR" in env) == (variable == "ECOS_VERILATOR")


def test_a_verilator_root_of_the_users_own_keeps_the_managed_binary_out(tmp_path) -> None:
    _managed(tmp_path, "verilator")
    env = _env(tmp_path, VERILATOR_ROOT="/opt/verilator-5.2/share/verilator")

    activate_managed_resources(env)

    assert "ECOS_VERILATOR" not in env
    assert env["VERILATOR_ROOT"] == "/opt/verilator-5.2/share/verilator"


def test_naming_the_managed_verilator_explicitly_still_gets_its_root(tmp_path) -> None:
    roots = _managed(tmp_path, "verilator")
    link = tmp_path / "my-verilator"
    link.symlink_to(roots["verilator"] / "bin" / "verilator")
    env = _env(tmp_path, ECOS_VERILATOR=str(link))

    activate_managed_resources(env)

    assert env["VERILATOR_ROOT"] == str(roots["verilator"] / "share" / "verilator")
    assert env["ECOS_VERILATOR"] == str(link)


# --- the other tools ----------------------------------------------------------


def test_a_slang_named_by_the_plain_variable_is_not_overridden(tmp_path) -> None:
    _managed(tmp_path, "slang")
    env = _env(tmp_path, SLANG="/opt/slang/bin/slang")

    activate_managed_resources(env)

    # ECOS_SLANG is looked at first; setting it would hide the user's choice.
    assert "ECOS_SLANG" not in env


@pytest.mark.parametrize("variable", ["YOSYS", "ECOS_YOSYS", "ECOS_OSS_CAD_BIN"])
def test_a_yosys_of_the_users_own_gets_no_managed_suite_directory(tmp_path, variable) -> None:
    _managed(tmp_path, "yosys")
    env = _env(tmp_path, **{variable: "/opt/oss-cad/bin/yosys"})

    activate_managed_resources(env)

    assert "CHIPCOMPILER_OSS_CAD_DIR" not in env and "ECOS_ELECTRON_OSS_CAD_DIR" not in env


def test_one_suite_directory_of_the_users_own_keeps_the_other_unset(tmp_path) -> None:
    _managed(tmp_path, "yosys")
    env = _env(tmp_path, CHIPCOMPILER_OSS_CAD_DIR="/opt/oss-cad")

    activate_managed_resources(env)

    assert env["CHIPCOMPILER_OSS_CAD_DIR"] == "/opt/oss-cad"
    assert "ECOS_ELECTRON_OSS_CAD_DIR" not in env


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("RISCV_PREFIX", "/opt/riscv32/bin/riscv32-unknown-elf-"),
        ("RISCV", "/opt/riscv32"),
        ("RISCV_TOOLCHAIN", "/opt/riscv32"),
    ],
)
def test_a_toolchain_of_the_users_own_is_not_mixed_with_the_managed_one(tmp_path, variable, value) -> None:
    _managed(tmp_path, "riscv-toolchain")
    env = _env(tmp_path, **{variable: value})

    activate_managed_resources(env)

    assert {name: env.get(name) for name in ("RISCV", "RISCV_TOOLCHAIN", "RISCV_PREFIX")} == {
        "RISCV": None,
        "RISCV_TOOLCHAIN": None,
        "RISCV_PREFIX": None,
        variable: value,
    }


def test_a_choice_for_one_tool_does_not_affect_the_others(tmp_path) -> None:
    roots = _managed(tmp_path, "verilator", "slang")
    env = _env(tmp_path, VERILATOR="/opt/verilator/bin/verilator")

    activate_managed_resources(env)

    assert "VERILATOR_ROOT" not in env
    assert env["ECOS_SLANG"] == str(roots["slang"] / "bin" / "slang")


def test_managed_tools_stay_on_path_behind_an_explicit_choice(tmp_path) -> None:
    roots = _managed(tmp_path, "verilator")
    env = _env(tmp_path, ECOS_VERILATOR="/opt/verilator/bin/verilator")

    activate_managed_resources(env)

    # PATH is only where tools are found when nothing names them.
    assert env["PATH"].split(os.pathsep) == [str(roots["verilator"] / "bin"), "/usr/bin"]
