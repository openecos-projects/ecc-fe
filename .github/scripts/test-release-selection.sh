#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELECTOR="${SCRIPT_DIR}/select-release-packages.sh"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ecc-fe-release-selection.XXXXXX")"
trap 'rm -rf "${TEMP_DIR}"' EXIT

run_case() {
  local name="$1"
  local expected="$2"
  local force_all="$3"
  shift 3
  local paths_file="${TEMP_DIR}/${name}.paths"
  local output_file="${TEMP_DIR}/${name}.output"
  printf '%s\n' "$@" > "${paths_file}"

  CHANGED_PATHS_FILE="${paths_file}" \
    FORCE_ALL="${force_all}" \
    GITHUB_OUTPUT="${output_file}" \
    bash "${SELECTOR}" "" HEAD

  local actual
  actual="$(sed -n 's/^matrix=//p' "${output_file}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "${name}: expected ${expected}, got ${actual}" >&2
    exit 1
  fi
  if grep -Eq '^(cpu-rtl|difftest-ref)=' "${output_file}"; then
    echo "${name}: GitHub output keys must use underscores" >&2
    exit 1
  fi
  expected_any=true
  [[ "${expected}" == "[]" ]] && expected_any=false
  if ! grep -Fx "any=${expected_any}" "${output_file}" >/dev/null; then
    echo "${name}: expected any=${expected_any}" >&2
    exit 1
  fi
}

run_case docs-only '[]' false docs/guide.md tests/test_cli.py
run_case runtime '["runtime"]' false fecompiler/engine.py
run_case runtime-packaging '["runtime"]' false ecc-fe.spec packaging/run_ecc_fe.py uv.lock
run_case split-assets '["soc","examples"]' false fecompiler/thirdparty/SoC/driver/main.cpp examples/ysyx_00000000/filelist.cpu.f
run_case difftest '["difftest-ref"]' false fecompiler/thirdparty/SoC/tools/riscv32-spike-so
run_case cpu-rtl '["cpu-rtl"]' false fecompiler/thirdparty/ibex
run_case release-infrastructure '["runtime","soc","cpu-rtl","difftest-ref","examples"]' false .github/scripts/package-release-assets.sh
run_case forced '["runtime","soc","cpu-rtl","difftest-ref","examples"]' true docs/guide.md

echo "Release package selection tests passed."
