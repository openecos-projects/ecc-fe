#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM_BIN="${ROOT}/build/soc_top"
IMAGE="${ROOT}/tests/out/min2.soc.bin"
MAX_CYCLES=2000000
WAVE=""
DIFF="${DIFF:-0}"
REF_SO="${REF_SO:-${ROOT}/tools/riscv32-spike-so}"
DIFF_IMAGE_OFFSET="${DIFF_IMAGE_OFFSET:-0}"
DIFF_RESET_VECTOR="${DIFF_RESET_VECTOR:-0x80000000}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sim-bin)
      SIM_BIN="$2"
      shift 2
      ;;
    --image)
      IMAGE="$2"
      shift 2
      ;;
    --max-cycles)
      MAX_CYCLES="$2"
      shift 2
      ;;
    --wave)
      WAVE="$2"
      shift 2
      ;;
    --diff)
      DIFF=1
      shift
      ;;
    --ref)
      REF_SO="$2"
      shift 2
      ;;
    --diff-image-offset)
      DIFF_IMAGE_OFFSET="$2"
      shift 2
      ;;
    --diff-reset-vector)
      DIFF_RESET_VECTOR="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--sim-bin <bin>] [--image <soc.bin>] [--max-cycles <n>] [--wave <file>] [--diff] [--ref <so>] [--diff-image-offset <n>] [--diff-reset-vector <n>]" >&2
      exit 1
      ;;
  esac
done

if [[ ! -x "${SIM_BIN}" ]]; then
  "${ROOT}/scripts/build_soc_sim.sh" "${SIM_BIN}"
fi

if [[ ! -f "${IMAGE}" ]]; then
  echo "image not found: ${IMAGE}" >&2
  exit 1
fi

CMD=("${SIM_BIN}" --image "${IMAGE}" --max-cycles "${MAX_CYCLES}")
if [[ -n "${WAVE}" ]]; then
  CMD+=(--wave "${WAVE}")
fi
if [[ "${DIFF}" == "1" ]]; then
  if [[ ! -f "${REF_SO}" ]]; then
    echo "difftest reference not found: ${REF_SO}" >&2
    exit 1
  fi
  CMD+=(--diff --ref "${REF_SO}" --diff-image-offset "${DIFF_IMAGE_OFFSET}" --diff-reset-vector "${DIFF_RESET_VECTOR}")
fi

"${CMD[@]}"
