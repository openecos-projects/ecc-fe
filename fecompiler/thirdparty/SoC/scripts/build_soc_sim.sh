#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${ROOT}/build"
MDIR="${BUILD_ROOT}/verilator"
OUT_BIN="${1:-${BUILD_ROOT}/soc_top}"
VERILATOR_BIN="${VERILATOR_BIN:-verilator}"
CPU_TOP_MODULE="${CPU_TOP_MODULE:-ysyx_00000000}"

find_default_cpu_root() {
  local candidate
  local roots="${ECOS_FE_RESOURCE_ROOTS:-}"
  if [[ -n "${roots}" ]]; then
    local -a resource_roots
    IFS=':' read -r -a resource_roots <<< "${roots}"
    for root in "${resource_roots[@]}"; do
      for candidate in "${root}/examples/ysyx_00000000" "${root}/ysyx_00000000"; do
        if [[ -f "${candidate}/filelist.cpu.f" ]]; then
          cd "${candidate}" && pwd
          return 0
        fi
      done
    done
  fi

  for candidate in \
    "${ROOT}/../../../examples/ysyx_00000000" \
    "${ROOT}/examples/ysyx_00000000" \
    "${PWD}/examples/ysyx_00000000"; do
    if [[ -f "${candidate}/filelist.cpu.f" ]]; then
      cd "${candidate}" && pwd
      return 0
    fi
  done

  return 1
}

if [[ -z "${CPU_ROOT:-}" ]]; then
  if ! CPU_ROOT="$(find_default_cpu_root)"; then
    echo "[build_soc_sim] CPU_ROOT is not set and examples/ysyx_00000000 was not found." >&2
    echo "[build_soc_sim] Set CPU_ROOT or install the ecc-fe-examples resource." >&2
    exit 1
  fi
fi
if [[ ! "${CPU_TOP_MODULE}" =~ ^[A-Za-z_][A-Za-z0-9_$]*$ ]]; then
  echo "[build_soc_sim] invalid CPU_TOP_MODULE: ${CPU_TOP_MODULE}" >&2
  exit 1
fi

if command -v nproc >/dev/null 2>&1; then
  JOBS_DEFAULT="$(nproc)"
else
  JOBS_DEFAULT=8
fi
JOBS="${JOBS:-${JOBS_DEFAULT}}"

mkdir -p "${BUILD_ROOT}"
mkdir -p "${MDIR}"

CPU_FILELIST="${CPU_ROOT}/filelist.cpu.f"
SOC_FILELIST="${ROOT}/filelist.soc.f"
if [[ ! -f "${CPU_FILELIST}" ]]; then
  echo "[build_soc_sim] missing CPU file list: ${CPU_FILELIST}" >&2
  exit 1
fi
if [[ ! -f "${SOC_FILELIST}" ]]; then
  echo "[build_soc_sim] missing SoC file list: ${SOC_FILELIST}" >&2
  exit 1
fi

SV_INPUTS=()
CPU_OPTIONS=()
CPU_RTL_INPUTS=()
CPU_SUPPORTS_DIFFTEST=0
while IFS= read -r rel_path; do
  [[ -z "${rel_path}" ]] && continue
  SV_INPUTS+=("${ROOT}/${rel_path}")
done < "${SOC_FILELIST}"
while IFS= read -r rel_path; do
  [[ -z "${rel_path}" || "${rel_path}" == \#* ]] && continue
  if [[ "${rel_path}" == +define+* ]]; then
    CPU_OPTIONS+=("${rel_path}")
    IFS='+' read -r -a cpu_defines <<< "${rel_path#+define+}"
    for cpu_define in "${cpu_defines[@]}"; do
      if [[ "${cpu_define%%=*}" == "ECOS_DIFFTEST" ]]; then
        CPU_SUPPORTS_DIFFTEST=1
      fi
    done
    continue
  fi
  [[ "${rel_path}" == +* || "${rel_path}" == -* ]] && continue
  CPU_RTL_INPUTS+=("${CPU_ROOT}/${rel_path}")
  SV_INPUTS+=("${CPU_ROOT}/${rel_path}")
done < "${CPU_FILELIST}"

if [[ "${#SV_INPUTS[@]}" -eq 0 ]]; then
  echo "[build_soc_sim] no Verilog inputs found" >&2
  exit 1
fi

if [[ "${CPU_SUPPORTS_DIFFTEST}" -eq 1 ]]; then
  CPU_HAS_DIFFTEST_IMPORT=0
  for cpu_rtl in "${CPU_RTL_INPUTS[@]}"; do
    if grep -Eq 'import[[:space:]]+"DPI-C"[[:space:]]+function[[:space:]]+int[[:space:]]+difftest_step' "${cpu_rtl}"; then
      CPU_HAS_DIFFTEST_IMPORT=1
      break
    fi
  done
  if [[ "${CPU_HAS_DIFFTEST_IMPORT}" -ne 1 ]]; then
    echo "[build_soc_sim] ECOS_DIFFTEST is declared, but no difftest_step DPI adapter was found." >&2
    exit 1
  fi
fi

CXXFLAGS_EXTRA="${NIX_CFLAGS_COMPILE:-} -std=c++17 -I${ROOT} -I${CPU_ROOT}"
LDFLAGS_EXTRA="$( (printf '%s\n' "${NIX_LDFLAGS:-}" | grep -oE -- '-L[^ ]+' | tr '\n' ' ') || true )"
DIFFTEST_SOURCE="${ROOT}/driver/difftest_stub.cpp"
if [[ "${CPU_SUPPORTS_DIFFTEST}" -eq 1 ]]; then
  DIFFTEST_SOURCE="${ROOT}/driver/difftest.cpp"
  LDFLAGS_EXTRA="${LDFLAGS_EXTRA} -ldl"
fi
VERILATOR_EXTRA_ARGS=(-CFLAGS "${CXXFLAGS_EXTRA}")
if [[ -n "${LDFLAGS_EXTRA// }" ]]; then
  VERILATOR_EXTRA_ARGS+=(-LDFLAGS "${LDFLAGS_EXTRA}")
fi

  "${VERILATOR_BIN}" \
  "${SV_INPUTS[@]}" \
  "${CPU_OPTIONS[@]}" \
  "${ROOT}/driver/dpi_mem.cpp" \
  "${DIFFTEST_SOURCE}" \
  "${ROOT}/driver/main.cpp" \
  -I"${ROOT}/perip/spi/rtl" \
  -I"${ROOT}/perip/uart16550/rtl" \
  -I"${CPU_ROOT}/rtl" \
  "+define+ECOS_CUSTOM_CPU_TOP=${CPU_TOP_MODULE}" \
  --Wno-lint --Wno-UNOPTFLAT --Wno-BLKANDNBLK --Wno-COMBDLY --Wno-MODDUP \
  "${VERILATOR_EXTRA_ARGS[@]}" \
  --timescale-override 1ns/1ps \
  --autoflush \
  --trace \
  --build -j "${JOBS}" --exe --timing --cc \
  --Mdir "${MDIR}" \
  --top-module ecos_sim_top \
  -o soc_top

cp -f "${MDIR}/soc_top" "${OUT_BIN}"
echo "[build_soc_sim] built: ${OUT_BIN}"
