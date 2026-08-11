#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST_DIR="${1:-dist}"
if (( $# > 0 )); then
  shift
fi
failures=0

fail() {
  echo "::error::$1" >&2
  failures=1
}

archive_path() {
  printf '%s/%s\n' "${DIST_DIR}" "$1"
}

require_archive() {
  local archive="$1"
  if [[ ! -f "${archive}" ]]; then
    fail "Missing release archive: ${archive}"
    return 1
  fi
}

require_archive_max_bytes() {
  local archive="$1"
  local max_bytes="$2"
  local size
  size="$(stat -c '%s' "${archive}")"
  if (( size > max_bytes )); then
    fail "${archive} is ${size} bytes; maximum allowed size is ${max_bytes} bytes"
  fi
}

require_entry() {
  local archive="$1"
  local entry="$2"
  if ! tar -tzf "${archive}" | grep -Fx "${entry}" >/dev/null; then
    fail "${archive} is missing required entry: ${entry}"
  fi
}

require_elf_entry() {
  local archive="$1"
  local entry="$2"
  if ! python3 - "${archive}" "${entry}" <<'PY'
import sys
import tarfile

archive, entry = sys.argv[1:]
with tarfile.open(archive, "r:gz") as bundle:
    member = bundle.extractfile(entry)
    if member is None or member.read(4) != b"\x7fELF":
        raise SystemExit(1)
PY
  then
    fail "${archive} entry is not a self-contained Linux executable: ${entry}"
  fi
}

forbid_entry_prefix() {
  local archive="$1"
  local prefix="$2"
  local matches
  matches="$(tar -tzf "${archive}" | grep -F "${prefix}" || true)"
  if [[ -n "${matches}" ]]; then
    fail "${archive} contains forbidden prefix: ${prefix}"
    echo "${matches}" >&2
  fi
}

check_common_forbidden_entries() {
  local archive="$1"
  local matches
  matches="$(
    tar -tzf "${archive}" | grep -E \
      '(^|/)(\.git|\.pytest_cache|\.mypy_cache|\.ruff_cache|__pycache__|workspace_projects|obj_dir)(/|$)|\.pyc$|(^|/)trace_hart_00\.dasm$|\.(vcd|fst|fsdb|vpd|ghw|wlf|lxt|lxt2)(\.gz)?$' \
      || true
  )"
  if [[ -n "${matches}" ]]; then
    fail "${archive} contains generated, cache, or waveform entries"
    echo "${matches}" >&2
  fi
}

check_archive() {
  local archive="$1"
  require_archive "${archive}" || return
  check_common_forbidden_entries "${archive}"
}

check_runtime() {
  local archive
  archive="$(archive_path ecc-fe-latest.tar.gz)"
  check_archive "${archive}" || return
  require_entry "${archive}" "ecc-fe-latest/bin/ecc-fe"
  require_elf_entry "${archive}" "ecc-fe-latest/bin/ecc-fe"
  require_entry "${archive}" "ecc-fe-latest/fecompiler/resources.py"
  forbid_entry_prefix "${archive}" "ecc-fe-latest/fecompiler/thirdparty/"
}

check_cpu_rtl_filelist_entries() {
  local archive="$1"
  local filelist line entry source_path relative archive_entry
  local thirdparty_root="${REPO_ROOT}/fecompiler/thirdparty/"

  for filelist in "${REPO_ROOT}"/fecompiler/adapters/*/filelist.cpu.f; do
    while IFS= read -r line; do
      entry="${line%%#*}"
      case "${entry}" in
        +incdir+*) entry="${entry#+incdir+}" ;;
        ../../thirdparty/*) ;;
        *) continue ;;
      esac
      source_path="$(realpath -m "$(dirname "${filelist}")/${entry}")"
      [[ "${source_path}" == "${thirdparty_root}"* ]] || continue
      relative="${source_path#${thirdparty_root}}"
      archive_entry="ecc-fe-cpu-rtl-latest/thirdparty/${relative}"
      if [[ -d "${source_path}" ]]; then
        archive_entry="${archive_entry}/"
      fi
      require_entry "${archive}" "${archive_entry}"
    done < "${filelist}"
  done
}

check_soc() {
  local archive
  archive="$(archive_path ecc-fe-soc-ysyx-am-latest.tar.gz)"
  check_archive "${archive}" || return
  require_entry "${archive}" "ecc-fe-soc-ysyx-am-latest/manifest.json"
  require_entry "${archive}" "ecc-fe-soc-ysyx-am-latest/catalog.json"
  require_entry "${archive}" "ecc-fe-soc-ysyx-am-latest/filelist.soc.f"
  require_entry "${archive}" "ecc-fe-soc-ysyx-am-latest/driver/main.cpp"
  forbid_entry_prefix "${archive}" "ecc-fe-soc-ysyx-am-latest/tools/riscv32-spike-so"
}

check_cpu_rtl() {
  local archive
  archive="$(archive_path ecc-fe-cpu-rtl-latest.tar.gz)"
  check_archive "${archive}" || return
  require_archive_max_bytes "${archive}" "$((50 * 1024 * 1024))"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/README"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/cv32e40p/rtl/cv32e40p_core.sv"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/cva6/core/cva6.sv"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/darkriscv/rtl/darkriscv.v"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/ibex/rtl/ibex_core.sv"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/learn-fpga/FemtoRV/RTL/PROCESSOR/femtorv32_electron.v"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/picorv32/picorv32.v"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/scr1/src/core/scr1_core_top.sv"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/serv/rtl/serv_top.v"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/vexriscv/verilog/VexRiscv_Min.v"
  check_cpu_rtl_filelist_entries "${archive}"
  forbid_entry_prefix "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/ibex/dv/"
  forbid_entry_prefix "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/learn-fpga/Basic/"
  forbid_entry_prefix "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/scr1/dependencies/"
  forbid_entry_prefix "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/serv/bench/"
}

check_difftest_ref() {
  local archive
  archive="$(archive_path ecc-fe-difftest-ref-latest.tar.gz)"
  check_archive "${archive}" || return
  require_entry "${archive}" "ecc-fe-difftest-ref-latest/tools/riscv32-spike-so"
}

check_examples() {
  local archive
  archive="$(archive_path ecc-fe-examples-latest.tar.gz)"
  check_archive "${archive}" || return
  require_entry "${archive}" "ecc-fe-examples-latest/examples/ysyx_00000000/filelist.cpu.f"
  require_entry "${archive}" "ecc-fe-examples-latest/examples/ysyx_00000000/rtl/ysyx_00000000.sv"
  require_entry "${archive}" "ecc-fe-examples-latest/examples/ysyx_00000000/rtl/ysyx_00000000_difftest.sv"
  forbid_entry_prefix "${archive}" "ecc-fe-examples-latest/examples/cl3/"
}

if (( $# == 0 )); then
  packages=(runtime soc cpu-rtl difftest-ref examples)
else
  packages=("$@")
fi

for package in "${packages[@]}"; do
  case "${package}" in
    runtime) check_runtime ;;
    soc) check_soc ;;
    cpu-rtl) check_cpu_rtl ;;
    difftest-ref) check_difftest_ref ;;
    examples) check_examples ;;
    *) fail "Unknown release package: ${package}" ;;
  esac
done

if [[ "${failures}" -ne 0 ]]; then
  exit 1
fi

echo "Release archive content checks passed."
