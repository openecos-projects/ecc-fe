#!/usr/bin/env bash
set -euo pipefail
umask 022
export LC_ALL=C
export TZ=UTC

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST_DIR="${1:-dist}"
shift || true

if (( $# == 0 )); then
  echo "Usage: $0 <dist-dir> <runtime|soc|cpu-rtl|difftest-ref|examples> [...]" >&2
  exit 2
fi

cd "${REPO_ROOT}"
mkdir -p "${DIST_DIR}"
DIST_DIR="$(cd "${DIST_DIR}" && pwd)"
WORK_DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ecc-fe-release.XXXXXX")"
trap 'rm -rf "${WORK_DIR}"' EXIT

SOURCE_COMMIT="${GITHUB_SHA:-$(git rev-parse HEAD)}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git show -s --format=%ct "${SOURCE_COMMIT}")}"
if [[ ! "${SOURCE_DATE_EPOCH}" =~ ^[0-9]+$ ]]; then
  echo "SOURCE_DATE_EPOCH must be a non-negative integer" >&2
  exit 2
fi
BUILT_AT="$(date -u -d "@${SOURCE_DATE_EPOCH}" '+%Y-%m-%dT%H:%M:%SZ')"

cleanup_package_root() {
  local root="$1"
  find "${root}" \
    \( -name '.git' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' -o -name '__pycache__' \) \
    -type d -prune -exec rm -rf {} +
  find "${root}" -name '.git' -type f -delete
  find "${root}" \( -name '*.pyc' -o -name 'trace_hart_00.dasm' \) -type f -delete
  find "${root}" \
    \( -name '*.vcd' -o -name '*.vcd.gz' -o -name '*.fst' -o -name '*.fsdb' -o -name '*.vpd' -o -name '*.ghw' -o -name '*.wlf' -o -name '*.lxt' -o -name '*.lxt2' \) \
    -type f -delete
}

write_metadata() {
  local metadata_path="$1"
  local package_name="$2"
  local archive="$3"
  local sha256="$4"
  local size="$5"
  local strip_prefix="$6"
  python3 - "${metadata_path}" "${package_name}" "${archive}" "${sha256}" "${size}" "${strip_prefix}" "${SOURCE_COMMIT}" "${BUILT_AT}" <<'PY'
import json
import sys
from pathlib import Path

path, name, archive, sha256, size, strip_prefix, commit, built_at = sys.argv[1:]
metadata = {
    "name": name,
    "version": "latest",
    "commit": commit,
    "sha256": sha256,
    "size": int(size),
    "built_at": built_at,
    "archive": archive,
    "strip_prefix": strip_prefix,
}
Path(path).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
PY
}

archive_package() {
  local package_name="$1"
  local root_name="$2"
  local root_path="${WORK_DIR}/${root_name}"
  local archive="${root_name}.tar.gz"
  local archive_path="${DIST_DIR}/${archive}"
  local sha256
  local size

  cleanup_package_root "${root_path}"
  tar \
    --sort=name \
    --format=gnu \
    --mtime="@${SOURCE_DATE_EPOCH}" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    -C "${WORK_DIR}" \
    -cf - "${root_name}" \
    | gzip -n > "${archive_path}"

  sha256="$(sha256sum "${archive_path}" | awk '{print $1}')"
  size="$(stat -c '%s' "${archive_path}")"
  printf '%s  %s\n' "${sha256}" "${archive}" > "${archive_path}.sha256"
  write_metadata \
    "${DIST_DIR}/${root_name}.metadata.json" \
    "${package_name}" \
    "${archive}" \
    "${sha256}" \
    "${size}" \
    "${root_name}"
}

prepare_runtime() {
  local root="${WORK_DIR}/ecc-fe-latest"
  mkdir -p "${root}"
  cp -a bin fecompiler README.md LICENSE pyproject.toml BUILD.bazel MODULE.bazel MODULE.bazel.lock "${root}/"
  rm -rf "${root}/fecompiler/thirdparty"
  archive_package ecc-fe ecc-fe-latest
}

prepare_soc() {
  local root="${WORK_DIR}/ecc-fe-soc-ysyx-am-latest"
  mkdir -p "${root}"
  cp -a fecompiler/thirdparty/SoC/. "${root}/"
  rm -f "${root}/tools/riscv32-spike-so"
  archive_package ecc-fe-soc-ysyx-am ecc-fe-soc-ysyx-am-latest
}

prepare_cpu_rtl() {
  local root="${WORK_DIR}/ecc-fe-cpu-rtl-latest"
  mkdir -p "${root}/thirdparty"
  cp -a \
    fecompiler/thirdparty/README \
    fecompiler/thirdparty/rtthread_prepare.py \
    fecompiler/thirdparty/cv32e40p \
    fecompiler/thirdparty/cva6 \
    fecompiler/thirdparty/darkriscv \
    fecompiler/thirdparty/ibex \
    fecompiler/thirdparty/learn-fpga \
    fecompiler/thirdparty/picorv32 \
    fecompiler/thirdparty/rt-thread-am \
    fecompiler/thirdparty/scr1 \
    fecompiler/thirdparty/serv \
    fecompiler/thirdparty/vexriscv \
    "${root}/thirdparty/"
  archive_package ecc-fe-cpu-rtl ecc-fe-cpu-rtl-latest
}

prepare_difftest_ref() {
  local root="${WORK_DIR}/ecc-fe-difftest-ref-latest"
  mkdir -p "${root}/tools"
  cp -a fecompiler/thirdparty/SoC/tools/riscv32-spike-so "${root}/tools/"
  archive_package ecc-fe-difftest-ref ecc-fe-difftest-ref-latest
}

prepare_examples() {
  local root="${WORK_DIR}/ecc-fe-examples-latest"
  mkdir -p "${root}"
  cp -a examples "${root}/"
  archive_package ecc-fe-examples ecc-fe-examples-latest
}

declare -A packaged=()
for package in "$@"; do
  if [[ "${packaged[${package}]:-false}" == "true" ]]; then
    echo "Duplicate package requested: ${package}" >&2
    exit 2
  fi
  packaged["${package}"]=true
  case "${package}" in
    runtime) prepare_runtime ;;
    soc) prepare_soc ;;
    cpu-rtl) prepare_cpu_rtl ;;
    difftest-ref) prepare_difftest_ref ;;
    examples) prepare_examples ;;
    *)
      echo "Unknown release package: ${package}" >&2
      exit 2
      ;;
  esac
done
