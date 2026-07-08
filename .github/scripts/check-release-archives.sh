#!/usr/bin/env bash
set -euo pipefail

DIST_DIR="${1:-dist}"
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
      '(^|/)(\.git|\.pytest_cache|__pycache__|workspace_projects|obj_dir)(/|$)|\.pyc$|(^|/)trace_hart_00\.dasm$|\.(vcd|fst|fsdb|vpd|ghw|wlf|lxt|lxt2)(\.gz)?$' \
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

runtime_archive="$(archive_path ecc-fe-latest.tar.gz)"
soc_archive="$(archive_path ecc-fe-soc-ysyx-am-latest.tar.gz)"
cpu_rtl_archive="$(archive_path ecc-fe-cpu-rtl-latest.tar.gz)"
difftest_archive="$(archive_path ecc-fe-difftest-ref-latest.tar.gz)"
examples_archive="$(archive_path ecc-fe-examples-latest.tar.gz)"

for archive in \
  "${runtime_archive}" \
  "${soc_archive}" \
  "${cpu_rtl_archive}" \
  "${difftest_archive}" \
  "${examples_archive}"; do
  check_archive "${archive}"
done

require_entry "${runtime_archive}" "ecc-fe-latest/bin/ecc-fe"
require_entry "${runtime_archive}" "ecc-fe-latest/fecompiler/resources.py"
forbid_entry_prefix "${runtime_archive}" "ecc-fe-latest/fecompiler/thirdparty/"

require_entry "${soc_archive}" "ecc-fe-soc-ysyx-am-latest/manifest.json"
require_entry "${soc_archive}" "ecc-fe-soc-ysyx-am-latest/catalog.json"
require_entry "${soc_archive}" "ecc-fe-soc-ysyx-am-latest/filelist.soc.f"
require_entry "${soc_archive}" "ecc-fe-soc-ysyx-am-latest/driver/main.cpp"
forbid_entry_prefix "${soc_archive}" "ecc-fe-soc-ysyx-am-latest/tools/riscv32-spike-so"

require_entry "${cpu_rtl_archive}" "ecc-fe-cpu-rtl-latest/thirdparty/README"
require_entry "${cpu_rtl_archive}" "ecc-fe-cpu-rtl-latest/thirdparty/rtthread_prepare.py"
for root in cv32e40p cva6 darkriscv ibex learn-fpga picorv32 rt-thread-am scr1 serv vexriscv; do
  require_entry "${cpu_rtl_archive}" "ecc-fe-cpu-rtl-latest/thirdparty/${root}/"
done

require_entry "${difftest_archive}" "ecc-fe-difftest-ref-latest/tools/riscv32-spike-so"

require_entry "${examples_archive}" "ecc-fe-examples-latest/examples/cl3/filelist.cpu.f"
require_entry "${examples_archive}" "ecc-fe-examples-latest/examples/cl3/cl3_verilog/cpu_top.sv"
forbid_entry_prefix "${examples_archive}" "ecc-fe-examples-latest/examples/cl3_std/"

if [[ "${failures}" -ne 0 ]]; then
  exit 1
fi

echo "Release archive content checks passed."
