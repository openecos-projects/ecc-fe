#!/usr/bin/env bash
set -euo pipefail

BEFORE_SHA="${1:-}"
AFTER_SHA="${2:-HEAD}"
FORCE_ALL="${FORCE_ALL:-false}"
CHANGED_PATHS_FILE="${CHANGED_PATHS_FILE:-}"

PACKAGE_ORDER=(runtime soc cpu-rtl difftest-ref examples)
declare -A selected=()

select_package() {
  selected["$1"]=true
}

select_all() {
  local package
  for package in "${PACKAGE_ORDER[@]}"; do
    select_package "${package}"
  done
}

classify_path() {
  local path="$1"
  case "${path}" in
    .github/workflows/release-latest.yml|\
    .github/scripts/package-release-assets.sh|\
    .github/scripts/select-release-packages.sh|\
    .github/scripts/check-release-archives.sh|\
    .github/scripts/check-no-tracked-waveforms.sh|\
    .github/scripts/test-release-selection.sh)
      select_all
      ;;
    bin|bin/*|packaging|packaging/*|ecc-fe.spec|fecompiler|README.md|LICENSE|pyproject.toml|uv.lock|BUILD.bazel|MODULE.bazel|MODULE.bazel.lock)
      select_package runtime
      ;;
    fecompiler/thirdparty/SoC/tools/riscv32-spike-so|fecompiler/thirdparty/SoC/tools/riscv32-spike-so/*)
      select_package difftest-ref
      ;;
    fecompiler/thirdparty/SoC)
      select_package soc
      select_package difftest-ref
      ;;
    fecompiler/thirdparty/SoC/*)
      select_package soc
      ;;
    fecompiler/thirdparty)
      select_package soc
      select_package cpu-rtl
      select_package difftest-ref
      ;;
    fecompiler/thirdparty/*|.gitmodules)
      select_package cpu-rtl
      ;;
    fecompiler/*)
      select_package runtime
      ;;
    examples|examples/*)
      select_package examples
      ;;
  esac
}

if [[ "${FORCE_ALL}" == "true" ]]; then
  select_all
elif [[ -n "${CHANGED_PATHS_FILE}" ]]; then
  while IFS= read -r path; do
    [[ -n "${path}" ]] && classify_path "${path}"
  done < "${CHANGED_PATHS_FILE}"
elif [[ -z "${BEFORE_SHA}" || "${BEFORE_SHA}" =~ ^0+$ ]] \
  || ! git cat-file -e "${BEFORE_SHA}^{commit}" 2>/dev/null; then
  select_all
else
  while IFS= read -r path; do
    [[ -n "${path}" ]] && classify_path "${path}"
  done < <(git diff --name-only "${BEFORE_SHA}" "${AFTER_SHA}" --)
fi

packages=()
matrix="["
separator=""
for package in "${PACKAGE_ORDER[@]}"; do
  enabled="${selected[${package}]:-false}"
  if [[ "${enabled}" == "true" ]]; then
    packages+=("${package}")
    matrix+="${separator}\"${package}\""
    separator=","
  fi
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    output_key="${package//-/_}"
    printf '%s=%s\n' "${output_key}" "${enabled}" >> "${GITHUB_OUTPUT}"
  fi
done
matrix+="]"

if (( ${#packages[@]} > 0 )); then
  any=true
  package_list="${packages[*]}"
else
  any=false
  package_list=""
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'any=%s\n' "${any}" >> "${GITHUB_OUTPUT}"
  printf 'packages=%s\n' "${package_list}" >> "${GITHUB_OUTPUT}"
  printf 'matrix=%s\n' "${matrix}" >> "${GITHUB_OUTPUT}"
else
  printf 'any=%s\n' "${any}"
  printf 'packages=%s\n' "${package_list}"
  printf 'matrix=%s\n' "${matrix}"
fi
