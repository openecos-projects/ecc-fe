#!/usr/bin/env bash
set -euo pipefail

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
  local archive root
  archive="$(archive_path ecc-fe-cpu-rtl-latest.tar.gz)"
  check_archive "${archive}" || return
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/README"
  require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/rtthread_prepare.py"
  for root in cv32e40p cva6 darkriscv ibex learn-fpga picorv32 rt-thread-am scr1 serv vexriscv; do
    require_entry "${archive}" "ecc-fe-cpu-rtl-latest/thirdparty/${root}/"
  done
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
  require_entry "${archive}" "ecc-fe-examples-latest/examples/cl3/filelist.cpu.f"
  require_entry "${archive}" "ecc-fe-examples-latest/examples/cl3/cl3_verilog/cpu_top.sv"
  forbid_entry_prefix "${archive}" "ecc-fe-examples-latest/examples/cl3_std/"
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
