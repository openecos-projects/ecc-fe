#!/usr/bin/env python
"""Checks for the shipped example collateral under examples/."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CL3_ROOT = REPO_ROOT / "examples" / "cl3"
CPU_FILELIST = CL3_ROOT / "filelist.cpu.f"
NESTED_FILELIST = CL3_ROOT / "cl3_verilog" / "filelist.f"


def _filelist_entries(path: Path) -> list[str]:
    entries: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def test_cl3_example_filelists_exist() -> None:
    assert CPU_FILELIST.exists()
    assert NESTED_FILELIST.exists()


def test_cl3_cpu_filelist_entries_exist() -> None:
    missing = [
        rel_path for rel_path in _filelist_entries(CPU_FILELIST)
        if not (CL3_ROOT / rel_path).exists()
    ]

    assert missing == []


def test_cl3_nested_filelist_entries_exist() -> None:
    nested_root = NESTED_FILELIST.parent
    missing = [
        rel_path for rel_path in _filelist_entries(NESTED_FILELIST)
        if not (nested_root / rel_path).exists()
    ]

    assert missing == []


def test_cl3_has_exactly_one_cpu_top() -> None:
    matches = [
        rel_path
        for rel_path in _filelist_entries(CPU_FILELIST)
        if (CL3_ROOT / rel_path).read_text(encoding="utf-8", errors="ignore").find(
            "module cpu_top"
        ) >= 0
    ]

    assert matches == ["cl3_verilog/cpu_top.sv"]


def test_cl3_cpu_filelist_does_not_ship_generated_wrapper() -> None:
    entries = _filelist_entries(CPU_FILELIST)

    assert "cl3_verilog/ysyx_00000000.sv" not in entries


def test_cl3_is_the_only_shipped_example_tree() -> None:
    assert not (REPO_ROOT / "examples" / "cl3_std").exists()


def test_cl3_difftest_wrapper_calls_dpi_bridge() -> None:
    source = (CL3_ROOT / "cl3_verilog" / "difftest_wrapper.sv").read_text(encoding="utf-8")

    assert 'import "DPI-C" function int difftest_step' in source
    assert "difftest_result = difftest_step(" in source
