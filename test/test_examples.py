#!/usr/bin/env python
"""Checks for the shipped example collateral under examples/."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CL3_ROOT = REPO_ROOT / "examples" / "cl3"
CPU_FILELIST = CL3_ROOT / "filelist.cpu.f"
NESTED_FILELIST = CL3_ROOT / "cl3_verilog" / "filelist.f"
CL3_STD_ROOT = REPO_ROOT / "examples" / "cl3_std"
CL3_STD_CPU_FILELIST = CL3_STD_ROOT / "filelist.cpu.f"
CL3_STD_NESTED_FILELIST = CL3_STD_ROOT / "cl3_verilog" / "filelist.f"


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


def test_cl3_std_example_filelists_exist() -> None:
    assert CL3_STD_CPU_FILELIST.exists()
    assert CL3_STD_NESTED_FILELIST.exists()


def test_cl3_std_cpu_filelist_entries_exist() -> None:
    missing = [
        rel_path for rel_path in _filelist_entries(CL3_STD_CPU_FILELIST)
        if not (CL3_STD_ROOT / rel_path).exists()
    ]

    assert missing == []


def test_cl3_std_nested_filelist_entries_exist() -> None:
    nested_root = CL3_STD_NESTED_FILELIST.parent
    missing = [
        rel_path for rel_path in _filelist_entries(CL3_STD_NESTED_FILELIST)
        if not (nested_root / rel_path).exists()
    ]

    assert missing == []


def test_cl3_std_has_exactly_one_standard_cpu_top() -> None:
    matches = [
        rel_path
        for rel_path in _filelist_entries(CL3_STD_CPU_FILELIST)
        if (CL3_STD_ROOT / rel_path).read_text(encoding="utf-8", errors="ignore").find(
            "module ecos_user_cpu_top"
        ) >= 0
    ]

    assert matches == ["cl3_verilog/ecos_user_cpu_top.sv"]
