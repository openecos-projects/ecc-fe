"""The memory behind the SoC harness answers narrow accesses the way AXI does."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[1] / "fecompiler/thirdparty/SoC/driver"

_PROGRAM = r"""
#include <cstdio>
#include "driver/dpi_mem.h"

int main() {
  const unsigned base = 0x80000100u;
  mem_write(base, 0xfu, 0x44332211u);
  mem_write(base + 4u, 0xfu, 0x88776655u);
  // A halfword and a byte read of an unaligned address: the bus word that
  // holds the address, every byte on its own lane.
  std::printf("%08llx\n", mem_read(base + 2u, 1u) & 0xffffffffull);
  std::printf("%08llx\n", mem_read(base + 3u, 0u) & 0xffffffffull);
  // A byte written to an unaligned address arrives on the lane of its strobe.
  mem_write(base + 2u, 0x4u, 0x00aa0000u);
  mem_write(base + 5u, 0x2u, 0x0000bb00u);
  std::printf("%08llx\n", mem_read(base, 2u) & 0xffffffffull);
  std::printf("%08llx\n", mem_read(base + 4u, 2u) & 0xffffffffull);
  return 0;
}
"""


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
@pytest.mark.skipif(not (DRIVER / "dpi_mem.cpp").is_file(), reason="SoC harness not present")
def test_narrow_accesses_keep_every_byte_on_its_lane(tmp_path) -> None:
    source = tmp_path / "probe.cpp"
    source.write_text(_PROGRAM, encoding="utf-8")
    binary = tmp_path / "probe"
    subprocess.run(
        ["g++", "-std=c++17", "-O0", f"-I{DRIVER.parent}", str(source), str(DRIVER / "dpi_mem.cpp"),
         "-o", str(binary)],
        check=True,
        capture_output=True,
    )

    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)

    assert result.stdout.split() == ["44332211", "44332211", "44aa2211", "8877bb55"]
