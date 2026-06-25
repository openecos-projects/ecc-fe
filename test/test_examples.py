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
