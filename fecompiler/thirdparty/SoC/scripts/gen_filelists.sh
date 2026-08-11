#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
    echo "[gen_filelists] CPU_ROOT is not set and examples/ysyx_00000000 was not found." >&2
    echo "[gen_filelists] Set CPU_ROOT or install the ecc-fe-examples resource." >&2
    exit 1
  fi
fi

CPU_LIST="${CPU_ROOT}/filelist.cpu.f"
SOC_LIST="${ROOT}/filelist.soc.f"

{
  echo "ysyxSoCFull.v"
  find "${ROOT}/perip" -type f -name '*.v' | sort | sed "s#^${ROOT}/##"
} > "${SOC_LIST}"

echo "[gen_filelists] generated: ${SOC_LIST}"
echo "[gen_filelists] using: ${CPU_LIST}"
