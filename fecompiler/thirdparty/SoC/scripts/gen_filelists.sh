#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CPU_ROOT="${CPU_ROOT:-/home/luyoung/ecc-fe/docs/examples/cl3}"

CPU_LIST="${CPU_ROOT}/filelist.cpu.f"
SOC_LIST="${ROOT}/filelist.soc.f"

{
  echo "ysyxSoCFull.v"
  echo "ysyx_00000000.sv"
  find "${ROOT}/perip" -type f -name '*.v' | sort | sed "s#^${ROOT}/##"
} > "${SOC_LIST}"

{
  echo "cl3_verilog/difftest_info_pkg.sv"
  echo "cl3_verilog/difftest.sv"
  sed 's#^#cl3_verilog/#' "${CPU_ROOT}/cl3_verilog/filelist.f"
} > "${CPU_LIST}"

echo "[gen_filelists] generated: ${SOC_LIST}"
echo "[gen_filelists] generated: ${CPU_LIST}"
