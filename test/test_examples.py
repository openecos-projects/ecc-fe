#!/usr/bin/env python
"""Checks for the shipped example collateral under examples/."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_ROOT = REPO_ROOT / "examples" / "ysyx_00000000"
CPU_FILELIST = EXAMPLE_ROOT / "filelist.cpu.f"
EXPECTED_RTL = [
    "rtl/ysyx_00000000_decode.sv",
    "rtl/ysyx_00000000_execute.sv",
    "rtl/ysyx_00000000_regfile.sv",
    "rtl/ysyx_00000000_csr.sv",
    "rtl/ysyx_00000000_core.sv",
    "rtl/ysyx_00000000_difftest.sv",
    "rtl/ysyx_00000000_axi.sv",
    "rtl/ysyx_00000000.sv",
]


def _filelist_entries(path: Path) -> list[str]:
    entries: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def test_ysyx_00000000_example_entry_files_exist() -> None:
    assert CPU_FILELIST.exists()
    assert (EXAMPLE_ROOT / "README.md").exists()


def test_ysyx_00000000_cpu_filelist_is_complete() -> None:
    assert _filelist_entries(CPU_FILELIST) == ["+define+ECOS_DIFFTEST", *EXPECTED_RTL]
    missing = [
        rel_path for rel_path in _filelist_entries(CPU_FILELIST)
        if not rel_path.startswith("+")
        if not (EXAMPLE_ROOT / rel_path).exists()
    ]

    assert missing == []


def test_ysyx_00000000_has_exactly_one_native_top() -> None:
    matches = [
        rel_path
        for rel_path in EXPECTED_RTL
        if re.search(
            r"\bmodule\s+ysyx_00000000\b",
            (EXAMPLE_ROOT / rel_path).read_text(encoding="utf-8", errors="ignore"),
        )
    ]

    assert matches == ["rtl/ysyx_00000000.sv"]


def test_ysyx_00000000_is_the_only_shipped_example_tree() -> None:
    assert [path.name for path in (REPO_ROOT / "examples").iterdir() if path.is_dir()] == [
        "ysyx_00000000"
    ]


def test_ysyx_00000000_example_exposes_difftest_adapter() -> None:
    sources = "\n".join(
        (EXAMPLE_ROOT / entry).read_text(encoding="utf-8", errors="ignore")
        for entry in EXPECTED_RTL
    )

    assert 'import "DPI-C" function int difftest_step' in sources
